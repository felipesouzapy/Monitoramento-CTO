from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
import json
import os
from db import (
    init_db,
    is_postgres_connected,
    test_connection,
    db_get_all_ctos,
    db_add_cto,
    db_update_cto,
    db_delete_cto,
    db_add_porta,
    db_update_porta,
    db_delete_porta
)

app = FastAPI(title="Monitoramento CTO")

# Inicializa banco de dados (Cria tabelas se PostgreSQL estiver disponível)
@app.on_event("startup")
def startup_db_client():
    init_db()

# Arquivos estáticos (CSS, JS, imagens)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Pasta dos templates HTML
templates = Jinja2Templates(directory="templates")


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


@app.get("/db-status")
async def db_status():
    connected, message = test_connection()
    return {
        "postgres_connected": connected,
        "message": message,
        "storage_mode": "PostgreSQL" if connected else "JSON Local (Fallback)"
    }


@app.get("/listar-ctos")
async def listar_ctos():
    ctos = db_get_all_ctos()
    return {"ctos": ctos}


@app.post("/nova-cto")
async def nova_cto(request: Request):
    data = await request.json()
    localizacao = data.get("localizacao", "Sem localização")
    quantidade_portas = data.get("quantidade_portas", "0")
    portas = data.get("portas", [])
    latitude = data.get("latitude", "")
    longitude = data.get("longitude", "")
    sinal = data.get("sinal", "")

    nova_cto_obj = db_add_cto(
        localizacao=localizacao,
        quantidade_portas=quantidade_portas,
        portas=portas,
        latitude=latitude,
        longitude=longitude,
        sinal=sinal
    )

    return {
        "message": "Nova CTO criada com sucesso!",
        "status": "success",
        "cto": nova_cto_obj
    }


@app.post("/adicionar-porta")
async def adicionar_porta(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")

    nova_porta = {
        "numero": data.get("numero"),
        "status": data.get("status"),
        "cliente": data.get("cliente", "-"),
        "plano": data.get("plano", "-"),
        "sinal": data.get("sinal", ""),
        "observacao": data.get("observacao", "-")
    }

    success = db_add_porta(cto_id, nova_porta)

    if not success:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Falha ao adicionar porta ou CTO não encontrada"}
        )

    return {
        "status": "success",
        "message": "Porta adicionada com sucesso!",
        "porta": nova_porta
    }


@app.post("/editar-cto")
async def editar_cto(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")

    localizacao = data.get("localizacao")
    quantidade_portas = data.get("quantidade_portas")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    sinal = data.get("sinal")

    success = db_update_cto(
        cto_id=cto_id,
        localizacao=localizacao,
        quantidade_portas=quantidade_portas,
        latitude=latitude,
        longitude=longitude,
        sinal=sinal
    )

    if not success:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "CTO não encontrada ou erro na atualização"}
        )

    return {
        "status": "success",
        "message": "CTO atualizada com sucesso!"
    }


@app.post("/excluir-cto")
async def excluir_cto(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")

    success = db_delete_cto(cto_id)

    if not success:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "CTO não encontrada"}
        )

    return {
        "status": "success",
        "message": "CTO excluída com sucesso!"
    }


@app.post("/editar-porta")
async def editar_porta(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")
    numero_original = data.get("numero_original")

    data_porta = {
        "numero": data.get("numero"),
        "status": data.get("status"),
        "cliente": data.get("cliente"),
        "plano": data.get("plano"),
        "sinal": data.get("sinal"),
        "observacao": data.get("observacao")
    }

    success, msg = db_update_porta(cto_id, numero_original, data_porta)

    if not success:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": msg}
        )

    return {
        "status": "success",
        "message": msg,
        "porta": data_porta
    }


@app.post("/excluir-porta")
async def excluir_porta(request: Request):
    data = await request.json()
    cto_id = data.get("cto_id")
    numero = data.get("numero")

    success = db_delete_porta(cto_id, numero)

    if not success:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Porta não encontrada ou CTO não encontrada"}
        )

    return {
        "status": "success",
        "message": "Porta excluída com sucesso!"
    }