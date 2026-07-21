from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
import json
import os

app = FastAPI(title="Monitoramento CTO")

# Arquivos estáticos (CSS, JS, imagens)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Pasta dos templates HTML
templates = Jinja2Templates(directory="templates")

# Arquivo onde serão salvos os dados
ARQUIVO = "dados.json"


def carregar_ctos():
    if not os.path.exists(ARQUIVO):
        with open(ARQUIVO, "w", encoding="utf-8") as f:
            json.dump([], f)

    with open(ARQUIVO, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_ctos():
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(ctos, f, ensure_ascii=False, indent=4)


# Carrega as CTOs salvas
ctos = carregar_ctos()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "titulo": "Monitoramento de CTO"
        }
    )


@app.post("/nova-cto")
async def nova_cto(request: Request):
    data = await request.json()
    localizacao = data.get("localizacao", "Sem localização")
    quantidade_portas = data.get("quantidade_portas", "0")
    portas = data.get("portas", [])
    latitude = data.get("latitude", "")
    longitude = data.get("longitude", "")

    nova_cto_obj = {
        "id": len(ctos) + 1,
        "nome": f"CTO {len(ctos) + 1}",
        "localizacao": localizacao,
        "quantidade_portas": quantidade_portas,
        "data_criacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "portas": portas,
        "latitude": latitude,
        "longitude": longitude
    }

    ctos.append(nova_cto_obj)
    salvar_ctos()

    return {
        "message": "Nova CTO criada com sucesso!",
        "status": "success",
        "cto": nova_cto_obj
    }


@app.post("/adicionar-porta")
async def adicionar_porta(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")

    # Encontrar a CTO pelo ID
    cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)

    if not cto:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "CTO não encontrada"}
        )

    nova_porta = {
        "numero": data.get("numero"),
        "status": data.get("status"),
        "cliente": data.get("cliente", "-"),
        "plano": data.get("plano", "-"),
        "observacao": data.get("observacao", "-")
    }
    
    cto["portas"].append(nova_porta)

    salvar_ctos()

    return {
        "status": "success",
        "message": "Porta adicionada com sucesso!",
        "porta": nova_porta
    }


@app.post("/editar-cto")
async def editar_cto(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")

    cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)

    if not cto:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "CTO não encontrada"}
        )

    if "localizacao" in data:
        cto["localizacao"] = data.get("localizacao")
    if "quantidade_portas" in data:
        cto["quantidade_portas"] = data.get("quantidade_portas")
    if "latitude" in data:
        cto["latitude"] = data.get("latitude")
    if "longitude" in data:
        cto["longitude"] = data.get("longitude")

    salvar_ctos()

    return {
        "status": "success",
        "message": "CTO atualizada com sucesso!",
        "cto": cto
    }


@app.post("/excluir-cto")
async def excluir_cto(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")

    cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)

    if not cto:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "CTO não encontrada"}
        )

    ctos.remove(cto)

    salvar_ctos()

    return {
        "status": "success",
        "message": "CTO excluída com sucesso!"
    }


@app.post("/editar-porta")
async def editar_porta(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")
    numero_original = data.get("numero_original")
    numero = data.get("numero")

    cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)

    if not cto:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "CTO não encontrada"}
        )

    porta = next((p for p in cto["portas"] if str(p["numero"]) == str(numero_original)), None)

    if not porta:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Porta não encontrada"}
        )

    if str(numero) != str(numero_original):
        porta_ocupada = next((p for p in cto["portas"] if str(p["numero"]) == str(numero)), None)
        if porta_ocupada:
            return JSONResponse(
                status_code=409,
                content={"status": "error", "message": f"A porta {numero} já está em uso"}
            )
        porta["numero"] = numero

    porta["status"] = data.get("status", porta["status"])
    porta["cliente"] = data.get("cliente", porta["cliente"])
    porta["plano"] = data.get("plano", porta["plano"])
    porta["observacao"] = data.get("observacao", porta["observacao"])

    return {
        "status": "success",
        "message": "Porta atualizada com sucesso!",
        "porta": porta
    }


@app.post("/excluir-porta")
async def excluir_porta(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")
    numero = data.get("numero")

    cto = next((c for c in ctos if str(c["id"]) == str(cto_id)), None)

    if not cto:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "CTO não encontrada"}
        )

    porta = next((p for p in cto["portas"] if str(p["numero"]) == str(numero)), None)

    if not porta:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Porta não encontrada"}
        )

    cto["portas"].remove(porta)

    salvar_ctos()
    return {
        "status": "success",
        "message": "Porta excluída com sucesso!"
    }


@app.get("/listar-ctos")
async def listar_ctos():
    return {"ctos": ctos}