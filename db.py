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

def db_get_all_ctos():
    if not is_postgres_connected():
        return load_json()
    
    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT * FROM ctos ORDER BY id ASC;")
        cto_rows = cur.fetchall()
        
        result = []
        for c in cto_rows:
            cur.execute("SELECT * FROM portas WHERE cto_id = %s ORDER BY CAST(numero AS INTEGER) ASC, id ASC;", (c["id"],))
            porta_rows = cur.fetchall()
            
            portas = []
            for p in porta_rows:
                portas.append({
                    "numero": str(p["numero"]),
                    "status": p["status"],
                    "cliente": p["cliente"],
                    "plano": p["plano"],
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

def db_add_cto(localizacao, quantidade_portas, portas, latitude="", longitude="", sinal=""):
    if not is_postgres_connected():
        ctos = load_json()
        new_id = (max([c["id"] for c in ctos], default=0)) + 1
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
        return new_cto

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
            cur.execute("""
                INSERT INTO portas (cto_id, numero, status, cliente, plano, sinal, observacao)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """, (
                new_id,
                str(p.get("numero", "")),
                p.get("status", "Livre"),
                p.get("cliente", "-"),
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
        # fallback json
        ctos = load_json()
        new_id = (max([c["id"] for c in ctos], default=0)) + 1
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
        return new_cto


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
        cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)
        if cto:
            cto["portas"].append(nova_porta)
            save_json(ctos)
            return True
        return False

    db_url = get_db_url()
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO portas (cto_id, numero, status, cliente, plano, sinal, observacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """, (
            cto_id,
            str(nova_porta.get("numero", "")),
            nova_porta.get("status", "Livre"),
            nova_porta.get("cliente", "-"),
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
        cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)
        if not cto: return False, "CTO não encontrada"
        porta = next((p for p in cto["portas"] if str(p["numero"]) == str(numero_original)), None)
        if not porta: return False, "Porta não encontrada"
        
        novo_numero = str(data_porta.get("numero", numero_original))
        if novo_numero != str(numero_original):
            if any(str(p["numero"]) == str(novo_numero) for p in cto["portas"]):
                return False, f"A porta {novo_numero} já está em uso"
            porta["numero"] = novo_numero
            
        porta["status"] = data_porta.get("status", porta["status"])
        porta["cliente"] = data_porta.get("cliente", porta["cliente"])
        porta["plano"] = data_porta.get("plano", porta["plano"])
        porta["sinal"] = data_porta.get("sinal", porta.get("sinal", ""))
        porta["observacao"] = data_porta.get("observacao", porta["observacao"])
        save_json(ctos)
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
            
        novo_numero = str(data_porta.get("numero", numero_original))
        if novo_numero != str(numero_original):
            cur.execute("SELECT id FROM portas WHERE cto_id = %s AND numero = %s;", (cto_id, novo_numero))
            if cur.fetchone():
                cur.close()
                conn.close()
                return False, f"A porta {novo_numero} já está em uso"
                
        cur.execute("""
            UPDATE portas 
            SET numero = %s, status = %s, cliente = %s, plano = %s, sinal = %s, observacao = %s
            WHERE id = %s;
        """, (
            novo_numero,
            data_porta.get("status", "Livre"),
            data_porta.get("cliente", "-"),
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
