import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("uvicorn")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

# Tenta carregar .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

JSON_FILE = "dados.json"
CLIENTS_JSON_FILE = "clientes.json"

def get_db_url():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    db = os.getenv("POSTGRES_DB", "monitoramento_cto")
    
    if os.getenv("POSTGRES_HOST") or os.getenv("POSTGRES_DB"):
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return None

def test_connection():
    if not PSYCOPG2_AVAILABLE:
        return False, "Driver psycopg2 não instalado"
    
    db_url = get_db_url()
    if not db_url:
        return False, "Variável de conexão PostgreSQL não configurada"
        
    try:
        conn = psycopg2.connect(db_url, connect_timeout=3)
        conn.close()
        return True, "Conectado ao PostgreSQL com sucesso"
    except Exception as e:
        return False, f"Falha ao conectar no PostgreSQL: {str(e)}"


def _migrar_clientes_json_para_postgres(cur):
    if not os.path.exists(CLIENTS_JSON_FILE):
        return
    try:
        with open(CLIENTS_JSON_FILE, "r", encoding="utf-8") as f:
            clients = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Erro ao importar cadastro local de clientes para PostgreSQL: {e}")
        return

    for cliente in clients:
        nome = str(cliente.get("nome") or "").strip()
        if not nome or nome == "-":
            continue
        cur.execute("""
            INSERT INTO clientes (nome, plano, pppoe, ppue)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (LOWER(TRIM(nome))) DO UPDATE
            SET plano = CASE
                    WHEN clientes.plano IS NULL OR clientes.plano IN ('', '-')
                    THEN EXCLUDED.plano ELSE clientes.plano END,
                pppoe = CASE
                    WHEN clientes.pppoe IS NULL OR clientes.pppoe = ''
                    THEN EXCLUDED.pppoe ELSE clientes.pppoe END,
                ppue = CASE
                    WHEN clientes.ppue IS NULL OR clientes.ppue = ''
                    THEN EXCLUDED.ppue ELSE clientes.ppue END;
        """, (
            nome,
            cliente.get("plano") or "-",
            cliente.get("pppoe") or "",
            cliente.get("ppue") or ""
        ))


def init_db():
    if not PSYCOPG2_AVAILABLE:
        return False
        
    db_url = get_db_url()
    if not db_url:
        return False

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ctos (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(100) NOT NULL,
                localizacao TEXT,
                quantidade_portas VARCHAR(20) DEFAULT '0',
                sinal VARCHAR(50) DEFAULT '',
                data_criacao VARCHAR(50),
                latitude VARCHAR(50) DEFAULT '',
                longitude VARCHAR(50) DEFAULT ''
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS portas (
                id SERIAL PRIMARY KEY,
                cto_id INT REFERENCES ctos(id) ON DELETE CASCADE,
                numero VARCHAR(20) NOT NULL,
                status VARCHAR(50) DEFAULT 'Livre',
                cliente VARCHAR(100) DEFAULT '-',
                plano VARCHAR(100) DEFAULT '-',
                sinal VARCHAR(50) DEFAULT '',
                observacao TEXT DEFAULT '-'
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(150) NOT NULL,
                plano VARCHAR(100) DEFAULT '-',
                pppoe TEXT DEFAULT '',
                ppue TEXT DEFAULT ''
            );
        """)

        cur.execute("""
            ALTER TABLE portas
            ADD COLUMN IF NOT EXISTS cliente_id INT
            REFERENCES clientes(id) ON DELETE SET NULL;
        """)
        cur.execute("ALTER TABLE portas ALTER COLUMN cliente TYPE VARCHAR(150);")

        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS clientes_nome_unique
            ON clientes (LOWER(TRIM(nome)));
        """)

        cur.execute("""
            INSERT INTO clientes (nome, plano)
            SELECT DISTINCT TRIM(p.cliente), COALESCE(NULLIF(p.plano, '-'), '-')
            FROM portas p
            WHERE p.cliente IS NOT NULL
              AND TRIM(p.cliente) NOT IN ('', '-')
            ON CONFLICT DO NOTHING;
        """)

        cur.execute("""
            UPDATE portas p
            SET cliente_id = c.id
            FROM clientes c
            WHERE p.cliente_id IS NULL
              AND p.cliente IS NOT NULL
              AND LOWER(TRIM(p.cliente)) = LOWER(TRIM(c.nome));
        """)
        
        # Garantir colunas 'sinal' se tabela já existia antes
        cur.execute("""
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='ctos' AND column_name='sinal') THEN
                    ALTER TABLE ctos ADD COLUMN sinal VARCHAR(50) DEFAULT '';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='portas' AND column_name='sinal') THEN
                    ALTER TABLE portas ADD COLUMN sinal VARCHAR(50) DEFAULT '';
                END IF;
            END $$;
        """)

        conn.commit()

        # Migrar dados do dados.json se o banco estiver vazio
        cur.execute("SELECT COUNT(*) FROM ctos;")
        count = cur.fetchone()[0]
        if count == 0 and os.path.exists(JSON_FILE):
            try:
                with open(JSON_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for cto in data:
                        cur.execute("""
                            INSERT INTO ctos (id, nome, localizacao, quantidade_portas, sinal, data_criacao, latitude, longitude)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING;
                        """, (
                            cto.get("id"),
                            cto.get("nome", f"CTO {cto.get('id')}"),
                            cto.get("localizacao", ""),
                            str(cto.get("quantidade_portas", 0)),
                            cto.get("sinal", ""),
                            cto.get("data_criacao", datetime.now().strftime("%d/%m/%Y %H:%M")),
                            cto.get("latitude", ""),
                            cto.get("longitude", "")
                        ))
                        
                        for p in cto.get("portas", []):
                            cur.execute("""
                                INSERT INTO portas (cto_id, numero, status, cliente, plano, sinal, observacao)
                                VALUES (%s, %s, %s, %s, %s, %s, %s);
                            """, (
                                cto.get("id"),
                                str(p.get("numero", "")),
                                p.get("status", "Livre"),
                                p.get("cliente", "-"),
                                p.get("plano", "-"),
                                p.get("sinal", ""),
                                p.get("observacao", "-")
                            ))
                conn.commit()
                # Atualiza a sequence do Postgres
                cur.execute("SELECT setval('ctos_id_seq', (SELECT COALESCE(MAX(id), 1) FROM ctos));")
                conn.commit()
            except Exception as migration_err:
                logger.error(f"Erro na migração inicial do JSON para PostgreSQL: {migration_err}")
                conn.rollback()

        _migrar_clientes_json_para_postgres(cur)
        cur.execute("""
            INSERT INTO clientes (nome, plano)
            SELECT DISTINCT TRIM(p.cliente), COALESCE(NULLIF(p.plano, '-'), '-')
            FROM portas p
            WHERE p.cliente IS NOT NULL
              AND TRIM(p.cliente) NOT IN ('', '-')
            ON CONFLICT DO NOTHING;
        """)
        cur.execute("""
            UPDATE portas p
            SET cliente_id = c.id
            FROM clientes c
            WHERE p.cliente_id IS NULL
              AND p.cliente IS NOT NULL
              AND LOWER(TRIM(p.cliente)) = LOWER(TRIM(c.nome));
        """)
        conn.commit()

        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Erro ao inicializar tabelas PostgreSQL: {e}")
        return False


def is_postgres_connected():
    connected, _ = test_connection()
    return connected

def load_json():
    if not os.path.exists(JSON_FILE):
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_json(data):
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_json_clients():
    if not os.path.exists(CLIENTS_JSON_FILE):
        return []
    try:
        with open(CLIENTS_JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Erro ao carregar cadastro local de clientes: {e}")
        raise RuntimeError("Cadastro local de clientes não pôde ser lido; arquivo preservado") from e


def save_json_clients(clients):
    with open(CLIENTS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(clients, f, ensure_ascii=False, indent=4)


def _postgres_cliente_id(cur, nome, plano="-", pppoe="", ppue=""):
    nome = str(nome or "").strip()
    if not nome or nome == "-":
        return None

    cur.execute(
        "SELECT id FROM clientes WHERE LOWER(TRIM(nome)) = LOWER(TRIM(%s));",
        (nome,)
    )
    row = cur.fetchone()
    if row:
        return row["id"] if isinstance(row, dict) else row[0]

    cur.execute("""
        INSERT INTO clientes (nome, plano, pppoe, ppue)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id;
    """, (nome, plano or "-", pppoe or "", ppue or ""))
    row = cur.fetchone()
    if row:
        return row["id"] if isinstance(row, dict) else row[0]

    cur.execute(
        "SELECT id FROM clientes WHERE LOWER(TRIM(nome)) = LOWER(TRIM(%s));",
        (nome,)
    )
    row = cur.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


def _normalizar_nome_cliente(nome):
    return str(nome or "").strip().casefold()


def _json_ensure_cliente(clients, nome, plano="-", pppoe="", ppue=""):
    nome = str(nome or "").strip()
    if not nome or nome == "-":
        return None

    normalized_name = _normalizar_nome_cliente(nome)
    cliente = next(
        (item for item in clients if _normalizar_nome_cliente(item.get("nome")) == normalized_name),
        None
    )
    if cliente:
        return cliente

    cliente = {
        "id": max((int(item.get("id", 0)) for item in clients), default=0) + 1,
        "nome": nome,
        "plano": plano or "-",
        "pppoe": pppoe or "",
        "ppue": ppue or ""
    }
    clients.append(cliente)
    return cliente


def _migrar_clientes_json():
    ctos = load_json()
    clients = load_json_clients()
    changed_clients = False
    changed_ctos = False

    for cto in ctos:
        for porta in cto.get("portas", []):
            nome = porta.get("cliente", "")
            if not nome or nome == "-":
                continue
            cliente = next(
                (item for item in clients if _normalizar_nome_cliente(item.get("nome")) == _normalizar_nome_cliente(nome)),
                None
            )
            if not cliente:
                cliente = _json_ensure_cliente(
                    clients,
                    nome,
                    porta.get("plano", "-"),
                    porta.get("pppoe", ""),
                    porta.get("ppue", "")
                )
                changed_clients = True
            if porta.get("cliente_id") != cliente["id"]:
                porta["cliente_id"] = cliente["id"]
                changed_ctos = True

    if changed_clients:
        save_json_clients(clients)
    if changed_ctos:
        save_json(ctos)
    return ctos, clients

def db_get_all_ctos():
    if not is_postgres_connected():
        ctos, _ = _migrar_clientes_json()
        return ctos
    
    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT * FROM ctos ORDER BY id ASC;")
        cto_rows = cur.fetchall()
        
        result = []
        for c in cto_rows:
            cur.execute("""
                SELECT p.*, cl.pppoe, cl.ppue
                FROM portas p
                LEFT JOIN clientes cl ON cl.id = p.cliente_id
                WHERE p.cto_id = %s
                ORDER BY CASE WHEN p.numero ~ '^[0-9]+$' THEN p.numero::INTEGER END NULLS LAST, p.id ASC;
            """, (c["id"],))
            porta_rows = cur.fetchall()
            
            portas = []
            for p in porta_rows:
                portas.append({
                    "numero": str(p["numero"]),
                    "status": p["status"],
                    "cliente": p["cliente"],
                    "cliente_id": p["cliente_id"],
                    "plano": p["plano"],
                    "pppoe": p["pppoe"] or "",
                    "ppue": p["ppue"] or "",
                    "sinal": p["sinal"] or "",
                    "observacao": p["observacao"]
                })
                
            result.append({
                "id": c["id"],
                "nome": c["nome"],
                "localizacao": c["localizacao"] or "",
                "quantidade_portas": str(c["quantidade_portas"] or "0"),
                "sinal": c["sinal"] or "",
                "data_criacao": c["data_criacao"] or "",
                "latitude": c["latitude"] or "",
                "longitude": c["longitude"] or "",
                "portas": portas
            })
            
        cur.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Erro ao buscar CTOs do Postgres: {e}")
        return load_json()


def _json_add_cto(localizacao, quantidade_portas, portas, latitude, longitude, sinal):
    ctos, clients = _migrar_clientes_json()
    vinculados = {
        str(porta.get("cliente_id"))
        for cto in ctos for porta in cto.get("portas", [])
        if porta.get("cliente_id") is not None
    }
    vinculados_novos = set()
    for porta in portas:
        cliente_id = porta.get("cliente_id")
        if cliente_id:
            cliente = next((c for c in clients if str(c["id"]) == str(cliente_id)), None)
            if not cliente:
                return None
        else:
            cliente = _json_ensure_cliente(
                clients,
                porta.get("cliente", ""),
                porta.get("plano", "-"),
                porta.get("pppoe", ""),
                porta.get("ppue", "")
            )
        if cliente:
            client_key = str(cliente["id"])
            if client_key in vinculados or client_key in vinculados_novos:
                return None
            vinculados_novos.add(client_key)
            porta["cliente_id"] = cliente["id"]
            porta["cliente"] = cliente["nome"]
            cliente["plano"] = porta.get("plano") or cliente.get("plano", "-")
            porta["plano"] = cliente["plano"]

    new_id = (max((int(cto["id"]) for cto in ctos), default=0)) + 1
    new_cto = {
        "id": new_id,
        "nome": f"CTO {new_id}",
        "localizacao": localizacao,
        "quantidade_portas": str(quantidade_portas),
        "sinal": sinal,
        "data_criacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "portas": portas,
        "latitude": latitude,
        "longitude": longitude
    }
    ctos.append(new_cto)
    save_json(ctos)
    save_json_clients(clients)
    return new_cto


def db_add_cto(localizacao, quantidade_portas, portas, latitude="", longitude="", sinal=""):
    if not is_postgres_connected():
        return _json_add_cto(localizacao, quantidade_portas, portas, latitude, longitude, sinal)

    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        data_criacao = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        cur.execute("""
            INSERT INTO ctos (nome, localizacao, quantidade_portas, sinal, data_criacao, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, ("TEMP", localizacao, str(quantidade_portas), sinal, data_criacao, latitude, longitude))
        
        new_id = cur.fetchone()["id"]
        nome = f"CTO {new_id}"
        
        cur.execute("UPDATE ctos SET nome = %s WHERE id = %s;", (nome, new_id))
        
        for p in portas:
            cliente_id = p.get("cliente_id")
            if cliente_id:
                cur.execute("SELECT id, nome, plano FROM clientes WHERE id = %s FOR UPDATE;", (cliente_id,))
                cliente = cur.fetchone()
                if not cliente:
                    conn.rollback()
                    cur.close()
                    conn.close()
                    return None
                p["cliente"] = cliente["nome"]
                p["plano"] = p.get("plano") or cliente["plano"] or "-"
                cur.execute("UPDATE clientes SET plano = %s WHERE id = %s;", (p["plano"], cliente_id))
            else:
                cliente_id = _postgres_cliente_id(
                    cur,
                    p.get("cliente", ""),
                    p.get("plano", "-"),
                    p.get("pppoe", ""),
                    p.get("ppue", "")
                )
            if cliente_id:
                cur.execute("SELECT id FROM clientes WHERE id = %s FOR UPDATE;", (cliente_id,))
                cur.execute("SELECT 1 FROM portas WHERE cliente_id = %s;", (cliente_id,))
                if cur.fetchone():
                    conn.rollback()
                    cur.close()
                    conn.close()
                    return None
            cur.execute("""
                INSERT INTO portas (cto_id, numero, status, cliente, cliente_id, plano, sinal, observacao)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                new_id,
                str(p.get("numero", "")),
                p.get("status", "Livre"),
                p.get("cliente", "-"),
                cliente_id,
                p.get("plano", "-"),
                p.get("sinal", ""),
                p.get("observacao", "-")
            ))
            
        conn.commit()
        cur.close()
        conn.close()
        
        return {
            "id": new_id,
            "nome": nome,
            "localizacao": localizacao,
            "quantidade_portas": str(quantidade_portas),
            "sinal": sinal,
            "data_criacao": data_criacao,
            "portas": portas,
            "latitude": latitude,
            "longitude": longitude
        }
    except Exception as e:
        logger.error(f"Erro ao inserir CTO no Postgres: {e}")
        return _json_add_cto(localizacao, quantidade_portas, portas, latitude, longitude, sinal)


def db_update_cto(cto_id, localizacao=None, quantidade_portas=None, latitude=None, longitude=None, sinal=None):
    if not is_postgres_connected():
        ctos = load_json()
        cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)
        if cto:
            if localizacao is not None: cto["localizacao"] = localizacao
            if quantidade_portas is not None: cto["quantidade_portas"] = str(quantidade_portas)
            if latitude is not None: cto["latitude"] = latitude
            if longitude is not None: cto["longitude"] = longitude
            if sinal is not None: cto["sinal"] = sinal
            save_json(ctos)
            return cto
        return None

    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        fields = []
        params = []
        if localizacao is not None:
            fields.append("localizacao = %s")
            params.append(localizacao)
        if quantidade_portas is not None:
            fields.append("quantidade_portas = %s")
            params.append(str(quantidade_portas))
        if latitude is not None:
            fields.append("latitude = %s")
            params.append(latitude)
        if longitude is not None:
            fields.append("longitude = %s")
            params.append(longitude)
        if sinal is not None:
            fields.append("sinal = %s")
            params.append(sinal)
            
        if fields:
            query = f"UPDATE ctos SET {', '.join(fields)} WHERE id = %s RETURNING *;"
            params.append(cto_id)
            cur.execute(query, tuple(params))
            conn.commit()
            
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Erro ao atualizar CTO no Postgres: {e}")
        return False

def db_delete_cto(cto_id):
    if not is_postgres_connected():
        ctos = load_json()
        cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)
        if cto:
            ctos.remove(cto)
            save_json(ctos)
            return True
        return False

    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("DELETE FROM ctos WHERE id = %s;", (cto_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Erro ao excluir CTO no Postgres: {e}")
        return False

def db_add_porta(cto_id, nova_porta):
    if not is_postgres_connected():
        ctos = load_json()
        clients = load_json_clients()
        cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)
        if cto:
            cliente_id = nova_porta.get("cliente_id")
            if cliente_id:
                if any(
                    str(porta.get("cliente_id")) == str(cliente_id)
                    for item in ctos for porta in item.get("portas", [])
                ):
                    return False
                cliente = next((item for item in clients if str(item["id"]) == str(cliente_id)), None)
                if not cliente:
                    return False
                nova_porta["cliente"] = cliente["nome"]
                cliente["plano"] = nova_porta.get("plano") or cliente.get("plano", "-")
                nova_porta["plano"] = cliente["plano"]
            else:
                cliente = _json_ensure_cliente(
                    clients,
                    nova_porta.get("cliente", ""),
                    nova_porta.get("plano", "-")
                )
                if cliente:
                    if any(
                        str(porta.get("cliente_id")) == str(cliente["id"])
                        for item in ctos for porta in item.get("portas", [])
                    ):
                        return False
                    nova_porta["cliente_id"] = cliente["id"]
            cto["portas"].append(nova_porta)
            save_json(ctos)
            save_json_clients(clients)
            return True
        return False

    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cliente_id = nova_porta.get("cliente_id")
        if cliente_id:
            cur.execute("SELECT id, nome, plano FROM clientes WHERE id = %s FOR UPDATE;", (cliente_id,))
            cliente = cur.fetchone()
            if not cliente:
                conn.rollback()
                cur.close()
                conn.close()
                return False
            cur.execute("SELECT 1 FROM portas WHERE cliente_id = %s;", (cliente_id,))
            if cur.fetchone():
                conn.rollback()
                cur.close()
                conn.close()
                return False
            nova_porta["cliente"] = cliente["nome"]
            nova_porta["plano"] = nova_porta.get("plano") or cliente["plano"] or "-"
            cur.execute("UPDATE clientes SET plano = %s WHERE id = %s;", (nova_porta["plano"], cliente_id))
        else:
            cliente_id = _postgres_cliente_id(
                cur, nova_porta.get("cliente", ""), nova_porta.get("plano", "-")
            )
            if cliente_id:
                cur.execute("SELECT id FROM clientes WHERE id = %s FOR UPDATE;", (cliente_id,))
                cur.execute("SELECT 1 FROM portas WHERE cliente_id = %s;", (cliente_id,))
                if cur.fetchone():
                    conn.rollback()
                    cur.close()
                    conn.close()
                    return False
        cur.execute("""
            INSERT INTO portas (cto_id, numero, status, cliente, cliente_id, plano, sinal, observacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """, (
            cto_id,
            str(nova_porta.get("numero", "")),
            nova_porta.get("status", "Livre"),
            nova_porta.get("cliente", "-"),
            cliente_id,
            nova_porta.get("plano", "-"),
            nova_porta.get("sinal", ""),
            nova_porta.get("observacao", "-")
        ))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Erro ao adicionar porta no Postgres: {e}")
        return False

def db_update_porta(cto_id, numero_original, data_porta):
    if not is_postgres_connected():
        ctos = load_json()
        clients = load_json_clients()
        cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)
        if not cto: return False, "CTO não encontrada"
        porta = next((p for p in cto["portas"] if str(p["numero"]) == str(numero_original)), None)
        if not porta: return False, "Porta não encontrada"

        cliente_id = data_porta.get("cliente_id")
        if cliente_id:
            if any(
                str(other.get("cliente_id")) == str(cliente_id)
                for item in ctos for other in item.get("portas", [])
                if other is not porta
            ):
                return False, "Este cliente já está vinculado a uma CTO"
            cliente = next((item for item in clients if str(item["id"]) == str(cliente_id)), None)
            if not cliente:
                return False, "Cliente não encontrado"
            data_porta["cliente"] = cliente["nome"]
            cliente["plano"] = data_porta.get("plano") or cliente.get("plano", "-")
            data_porta["plano"] = cliente["plano"]
        else:
            cliente = _json_ensure_cliente(
                clients,
                data_porta.get("cliente", porta.get("cliente", "")),
                data_porta.get("plano", porta.get("plano", "-"))
            )
            cliente_id = cliente["id"] if cliente else None
            if cliente_id and any(
                str(other.get("cliente_id")) == str(cliente_id)
                for item in ctos for other in item.get("portas", [])
                if other is not porta
            ):
                return False, "Este cliente já está vinculado a uma CTO"

        novo_numero = str(data_porta.get("numero", numero_original))
        if novo_numero != str(numero_original):
            if any(str(p["numero"]) == str(novo_numero) for p in cto["portas"]):
                return False, f"A porta {novo_numero} já está em uso"
            porta["numero"] = novo_numero
            
        porta["status"] = data_porta.get("status", porta["status"])
        porta["cliente"] = data_porta.get("cliente", porta["cliente"])
        porta["cliente_id"] = cliente_id
        porta["plano"] = data_porta.get("plano", porta["plano"])
        porta["sinal"] = data_porta.get("sinal", porta.get("sinal", ""))
        porta["observacao"] = data_porta.get("observacao", porta["observacao"])
        save_json(ctos)
        save_json_clients(clients)
        return True, "Porta atualizada com sucesso"

    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT id FROM portas WHERE cto_id = %s AND numero = %s;", (cto_id, str(numero_original)))
        porta_row = cur.fetchone()
        if not porta_row:
            cur.close()
            conn.close()
            return False, "Porta não encontrada"

        cliente_id = data_porta.get("cliente_id")
        if cliente_id:
            cur.execute("SELECT nome FROM clientes WHERE id = %s;", (cliente_id,))
            cliente_row = cur.fetchone()
            if not cliente_row:
                conn.rollback()
                cur.close()
                conn.close()
                return False, "Cliente não encontrado"
            cur.execute(
                "SELECT 1 FROM portas WHERE cliente_id = %s AND id <> %s;",
                (cliente_id, porta_row["id"])
            )
            if cur.fetchone():
                conn.rollback()
                cur.close()
                conn.close()
                return False, "Este cliente já está vinculado a uma CTO"
            data_porta["cliente"] = cliente_row["nome"]
            data_porta["plano"] = data_porta.get("plano") or "-"
            cur.execute("UPDATE clientes SET plano = %s WHERE id = %s;", (data_porta["plano"], cliente_id))
        else:
            cliente_id = _postgres_cliente_id(
                cur,
                data_porta.get("cliente", ""),
                data_porta.get("plano", "-")
            )
            if cliente_id:
                cur.execute("SELECT id FROM clientes WHERE id = %s FOR UPDATE;", (cliente_id,))
                cur.execute(
                    "SELECT 1 FROM portas WHERE cliente_id = %s AND id <> %s;",
                    (cliente_id, porta_row["id"])
                )
                if cur.fetchone():
                    conn.rollback()
                    cur.close()
                    conn.close()
                    return False, "Este cliente já está vinculado a uma CTO"
            
        novo_numero = str(data_porta.get("numero", numero_original))
        if novo_numero != str(numero_original):
            cur.execute("SELECT id FROM portas WHERE cto_id = %s AND numero = %s;", (cto_id, novo_numero))
            if cur.fetchone():
                cur.close()
                conn.close()
                return False, f"A porta {novo_numero} já está em uso"
                
        cur.execute("""
            UPDATE portas 
            SET numero = %s, status = %s, cliente = %s, cliente_id = %s,
                plano = %s, sinal = %s, observacao = %s
            WHERE id = %s;
        """, (
            novo_numero,
            data_porta.get("status", "Livre"),
            data_porta.get("cliente", "-"),
            cliente_id,
            data_porta.get("plano", "-"),
            data_porta.get("sinal", ""),
            data_porta.get("observacao", "-"),
            porta_row["id"]
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        return True, "Porta atualizada com sucesso"
    except Exception as e:
        logger.error(f"Erro ao editar porta no Postgres: {e}")
        return False, f"Erro no banco de dados: {e}"

def db_delete_porta(cto_id, numero):
    if not is_postgres_connected():
        ctos = load_json()
        cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)
        if not cto: return False
        porta = next((p for p in cto["portas"] if str(p["numero"]) == str(numero)), None)
        if not porta: return False
        cto["portas"].remove(porta)
        save_json(ctos)
        return True

    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("DELETE FROM portas WHERE cto_id = %s AND numero = %s;", (cto_id, str(numero)))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Erro ao deletar porta no Postgres: {e}")
        return False


def db_get_all_clientes():
    if not is_postgres_connected():
        _, clients = _migrar_clientes_json()
        ctos = load_json()
        links = {}
        for cto in ctos:
            for porta in cto.get("portas", []):
                cliente_id = porta.get("cliente_id")
                if cliente_id is not None and str(cliente_id) not in links:
                    links[str(cliente_id)] = {
                        "cto_id": cto["id"],
                        "cto_nome": cto.get("nome", ""),
                        "porta_numero": porta.get("numero", "")
                    }
        return [
            {
                **cliente,
                **links.get(str(cliente["id"]), {
                    "cto_id": None,
                    "cto_nome": None,
                    "porta_numero": None
                })
            }
            for cliente in clients
        ]

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(get_db_url())
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT cl.id, cl.nome, cl.plano, cl.pppoe, cl.ppue,
                   link.cto_id, link.cto_nome, link.porta_numero
            FROM clientes cl
            LEFT JOIN LATERAL (
                SELECT p.cto_id, c.nome AS cto_nome, p.numero AS porta_numero
                FROM portas p
                JOIN ctos c ON c.id = p.cto_id
                WHERE p.cliente_id = cl.id
                ORDER BY p.id
                LIMIT 1
            ) link ON TRUE
            ORDER BY LOWER(cl.nome), cl.id;
        """)
        clients = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()
        return clients
    except Exception as e:
        logger.error(f"Erro ao buscar clientes do PostgreSQL: {e}")
        return []


def db_add_cliente(nome, plano="-", pppoe="", ppue=""):
    nome = str(nome or "").strip()
    if not nome:
        return False, "Informe o nome do cliente", None

    if not is_postgres_connected():
        _, clients = _migrar_clientes_json()
        if any(_normalizar_nome_cliente(c.get("nome")) == _normalizar_nome_cliente(nome) for c in clients):
            return False, "Já existe um cliente com esse nome", None
        cliente = _json_ensure_cliente(clients, nome, plano, pppoe, ppue)
        save_json_clients(clients)
        return True, "Cliente cadastrado com sucesso", cliente

    try:
        conn = psycopg2.connect(get_db_url())
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT 1 FROM clientes WHERE LOWER(TRIM(nome)) = LOWER(TRIM(%s));",
            (nome,)
        )
        if cur.fetchone():
            conn.rollback()
            cur.close()
            conn.close()
            return False, "Já existe um cliente com esse nome", None
        cliente_id = _postgres_cliente_id(cur, nome, plano, pppoe, ppue)
        cur.execute(
            "SELECT id, nome, plano, pppoe, ppue FROM clientes WHERE id = %s;",
            (cliente_id,)
        )
        cliente = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if cliente:
            return True, "Cliente cadastrado com sucesso", dict(cliente)
        return False, "Já existe um cliente com esse nome", None
    except Exception as e:
        logger.error(f"Erro ao cadastrar cliente no PostgreSQL: {e}")
        return False, "Erro ao cadastrar cliente no banco de dados", None


def db_update_cliente(cliente_id, nome, plano="-", pppoe="", ppue=""):
    nome = str(nome or "").strip()
    if not nome:
        return False, "Informe o nome do cliente"

    if not is_postgres_connected():
        _, clients = _migrar_clientes_json()
        cliente = next((c for c in clients if str(c["id"]) == str(cliente_id)), None)
        if not cliente:
            return False, "Cliente não encontrado"
        if any(
            str(other["id"]) != str(cliente_id)
            and _normalizar_nome_cliente(other.get("nome")) == _normalizar_nome_cliente(nome)
            for other in clients
        ):
            return False, "Já existe um cliente com esse nome"
        cliente.update({
            "nome": nome,
            "plano": plano or "-",
            "pppoe": pppoe or "",
            "ppue": ppue or ""
        })
        save_json_clients(clients)
        ctos = load_json()
        for cto in ctos:
            for porta in cto.get("portas", []):
                if str(porta.get("cliente_id")) == str(cliente_id):
                    porta["cliente"] = nome
                    porta["plano"] = plano or "-"
        save_json(ctos)
        return True, "Cliente atualizado com sucesso"

    try:
        conn = psycopg2.connect(get_db_url())
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM clientes WHERE LOWER(TRIM(nome)) = LOWER(TRIM(%s)) AND id <> %s;",
            (nome, cliente_id)
        )
        if cur.fetchone():
            conn.rollback()
            cur.close()
            conn.close()
            return False, "Já existe um cliente com esse nome"
        cur.execute("""
            UPDATE clientes
            SET nome = %s, plano = %s, pppoe = %s, ppue = %s
            WHERE id = %s;
        """, (nome, plano or "-", pppoe or "", ppue or "", cliente_id))
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            conn.close()
            return False, "Cliente não encontrado"
        cur.execute("UPDATE portas SET cliente = %s, plano = %s WHERE cliente_id = %s;", (nome, plano or "-", cliente_id))
        conn.commit()
        cur.close()
        conn.close()
        return True, "Cliente atualizado com sucesso"
    except Exception as e:
        logger.error(f"Erro ao atualizar cliente no PostgreSQL: {e}")
        return False, "Erro ao atualizar cliente no banco de dados"


def db_desvincular_cliente(cliente_id):
    if not is_postgres_connected():
        ctos, clients = _migrar_clientes_json()
        cliente = next((c for c in clients if str(c["id"]) == str(cliente_id)), None)
        if not cliente:
            return False, "Cliente não encontrado"
        porta = next(
            (p for cto in ctos for p in cto.get("portas", [])
             if str(p.get("cliente_id")) == str(cliente_id)),
            None
        )
        if not porta:
            return False, "Cliente não está vinculado a uma CTO"
        porta.update({"cliente_id": None, "cliente": "-", "plano": "-", "status": "Livre"})
        save_json(ctos)
        return True, "Cliente removido da CTO e mantido no cadastro"

    try:
        conn = psycopg2.connect(get_db_url())
        cur = conn.cursor()
        cur.execute("""
            UPDATE portas
            SET cliente_id = NULL, cliente = '-', plano = '-', status = 'Livre'
            WHERE id = (
                SELECT id FROM portas WHERE cliente_id = %s ORDER BY id LIMIT 1
            )
            RETURNING id;
        """, (cliente_id,))
        if not cur.fetchone():
            conn.rollback()
            cur.close()
            conn.close()
            return False, "Cliente não está vinculado a uma CTO"
        conn.commit()
        cur.close()
        conn.close()
        return True, "Cliente removido da CTO e mantido no cadastro"
    except Exception as e:
        logger.error(f"Erro ao desvincular cliente no PostgreSQL: {e}")
        return False, "Erro ao remover cliente da CTO"


def db_transferir_cliente(cliente_id, cto_origem_id, cto_destino_id, numero_destino):
    if str(cto_origem_id) == str(cto_destino_id):
        return False, "Escolha uma CTO diferente da origem"

    if not is_postgres_connected():
        ctos, clients = _migrar_clientes_json()
        cliente = next((c for c in clients if str(c["id"]) == str(cliente_id)), None)
        origem = next((c for c in ctos if str(c["id"]) == str(cto_origem_id)), None)
        destino = next((c for c in ctos if str(c["id"]) == str(cto_destino_id)), None)
        if not cliente or not origem or not destino:
            return False, "Cliente ou CTO não encontrada"
        vinculos = [
            (cto, porta) for cto in ctos for porta in cto.get("portas", [])
            if str(porta.get("cliente_id")) == str(cliente_id)
        ]
        if len(vinculos) != 1 or vinculos[0][0] is not origem:
            return False, "O cliente não está vinculado à CTO de origem"

        numero_destino = str(numero_destino)
        capacidade = int(destino.get("quantidade_portas") or 0)
        if not numero_destino.isdigit() or not 1 <= int(numero_destino) <= capacidade:
            return False, "A porta selecionada não pertence à CTO de destino"
        porta_destino = next(
            (p for p in destino.get("portas", []) if str(p.get("numero")) == numero_destino),
            None
        )
        if porta_destino and (
            porta_destino.get("status") != "Livre"
            or porta_destino.get("cliente_id")
            or porta_destino.get("cliente") not in (None, "", "-")
        ):
            return False, "A porta selecionada está ocupada"

        porta_origem = vinculos[0][1]
        if porta_destino is None:
            porta_destino = {
                "numero": numero_destino,
                "status": "Livre",
                "cliente": "-",
                "plano": "-",
                "sinal": "",
                "observacao": "-"
            }
            destino.setdefault("portas", []).append(porta_destino)

        porta_origem.update({
            "cliente_id": None,
            "cliente": "-",
            "plano": "-",
            "status": "Livre",
            "sinal": ""
        })
        porta_destino.update({
            "cliente_id": cliente["id"],
            "cliente": cliente["nome"],
            "plano": cliente.get("plano", "-"),
            "status": "Ocupada"
        })
        save_json(ctos)
        return True, "Cliente transferido com sucesso"

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(get_db_url())
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, nome, plano FROM clientes WHERE id = %s FOR UPDATE;", (cliente_id,))
        cliente = cur.fetchone()
        if not cliente:
            conn.rollback()
            return False, "Cliente não encontrado"

        cur.execute("SELECT id, cto_id, numero FROM portas WHERE cliente_id = %s FOR UPDATE;", (cliente_id,))
        vinculos = cur.fetchall()
        if len(vinculos) != 1 or str(vinculos[0]["cto_id"]) != str(cto_origem_id):
            conn.rollback()
            return False, "O cliente não está vinculado à CTO de origem"

        cur.execute("SELECT quantidade_portas FROM ctos WHERE id = %s;", (cto_destino_id,))
        destino = cur.fetchone()
        if not destino:
            conn.rollback()
            return False, "CTO de destino não encontrada"
        numero_destino = str(numero_destino)
        capacidade = int(destino["quantidade_portas"] or 0)
        if not numero_destino.isdigit() or not 1 <= int(numero_destino) <= capacidade:
            conn.rollback()
            return False, "A porta selecionada não pertence à CTO de destino"

        cur.execute("""
            SELECT id, status, cliente_id, cliente
            FROM portas WHERE cto_id = %s AND numero = %s FOR UPDATE;
        """, (cto_destino_id, numero_destino))
        porta_destino = cur.fetchone()
        if porta_destino and (
            porta_destino["status"] != "Livre"
            or porta_destino["cliente_id"] is not None
            or porta_destino["cliente"] not in (None, "", "-")
        ):
            conn.rollback()
            return False, "A porta selecionada está ocupada"

        cur.execute("""
            UPDATE portas
            SET cliente_id = NULL, cliente = '-', plano = '-', status = 'Livre', sinal = ''
            WHERE id = %s;
        """, (vinculos[0]["id"],))
        if porta_destino:
            cur.execute("""
                UPDATE portas
                SET cliente_id = %s, cliente = %s, plano = %s, status = 'Ocupada'
                WHERE id = %s;
            """, (cliente["id"], cliente["nome"], cliente["plano"] or "-", porta_destino["id"]))
        else:
            cur.execute("""
                INSERT INTO portas (cto_id, numero, status, cliente, cliente_id, plano)
                VALUES (%s, %s, 'Ocupada', %s, %s, %s);
            """, (cto_destino_id, numero_destino, cliente["nome"], cliente["id"], cliente["plano"] or "-"))

        conn.commit()
        return True, "Cliente transferido com sucesso"
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Erro ao transferir cliente no PostgreSQL: {e}")
        return False, "Erro ao transferir cliente no banco de dados"
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
