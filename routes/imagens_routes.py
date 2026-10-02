"""Rotas do recurso 'imagens' (galeria): leitura pública e CRUD administrativo."""
from flask import Blueprint, request, jsonify

from auth import token_obrigatorio
from helpers import supabase

imagens_bp = Blueprint('imagens', __name__)


@imagens_bp.route('/imagens', methods=['GET'])
def buscar_imagens():
    try:
        dados_imagens = supabase.table("imagens").select("*").execute()
        if dados_imagens and dados_imagens.data is not None:
            if len(dados_imagens.data) > 0:
                return jsonify(dados_imagens.data), 200

            # Se a tabela imagens estiver vazia, busca os registros da tabela evento no Supabase
            dados_eventos = supabase.table("evento").select("*").execute()
            if dados_eventos and dados_eventos.data and len(dados_eventos.data) > 0:
                eventos_como_imagens = []
                for ev in dados_eventos.data:
                    eventos_como_imagens.append({
                        "id": ev.get("id"),
                        "titulo": ev.get("nome"),
                        "periodo": ev.get("periodo"),
                        "ano": ev.get("ano_inicio") or ev.get("ano"),
                        "contexto": ev.get("acontecimento"),
                        "pintor": ev.get("lugar"),
                        "url": ev.get("imagemUrl") or ev.get("imagem") or ""
                    })
                return jsonify(eventos_como_imagens), 200

            return jsonify([]), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Erro ao buscar as imagens"
            }), 502
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao buscar as imagens: {str(e)}"
        }), 502


@imagens_bp.route('/imagens', methods=['POST'])
@token_obrigatorio
def adicionar_imagens():
    dados = request.get_json() or {}

    titulo = dados.get("titulo", "").strip()
    url = dados.get("url", "").strip()

    if not titulo or not url:
        return jsonify({
            "status": "error",
            "message": "Título e URL da imagem são obrigatórios."
        }), 400

    try:
        dados_imagens = {
            "titulo": titulo,
            "pintor": str(dados.get("pintor", "Desconhecido")),
            "periodo": str(dados.get("periodo", "Geral")),
            "ano": str(dados.get("ano", "")),
            "contexto": str(dados.get("contexto", "")),
            "url": url
        }

        # Insere os dados na tabela imagens
        supabase.table("imagens").insert([dados_imagens]).execute()
        return jsonify({
            "status": "success",
            "message": "Imagem adicionada com sucesso!",
            "data": dados_imagens
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao adicionar a imagem: {str(e)}"
        }), 500


@imagens_bp.route('/imagens/<int:id>', methods=['DELETE'])
@token_obrigatorio
def deletar_imagens(id):
    if not id:
        return jsonify({
            "status": "error",
            "message": "ID inválido"
        }), 400
    try:
        supabase.table('imagens').delete().eq('id', id).execute()
        return jsonify({
            "status": "success",
            "message": "Imagem deletada com sucesso!",
            "data": id
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao deletar a imagem: {str(e)}"
        }), 500


@imagens_bp.route('/imagens/<int:id>', methods=['PATCH'])
@token_obrigatorio
def editar_imagens(id):
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Preencha os campos"
        }), 400

    campos_editaveis = ['titulo', 'pintor', 'periodo', 'ano', 'contexto', 'url']

    campos_para_atualizar = {
        campo: dados[campo]
        for campo in campos_editaveis
        if campo in dados
    }

    if not campos_para_atualizar:
        return jsonify({
            "status": "error",
            "message": "Nenhum campo válido para atualizar foi fornecido"
        }), 400

    try:
        supabase.table('imagens').update(campos_para_atualizar).eq('id', id).execute()

        return jsonify({
            "status": "success",
            "message": "Imagem atualizada com sucesso!",
            "data": campos_para_atualizar
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao editar a imagem: {str(e)}"
        }), 500
