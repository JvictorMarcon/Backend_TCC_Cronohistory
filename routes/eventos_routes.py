"""Rotas do recurso 'evento': leitura pública e CRUD administrativo."""
import json
from flask import Blueprint, request, jsonify

from auth import token_obrigatorio
from helpers import supabase, dados_eventos, generate_history

eventos_bp = Blueprint('eventos', __name__)


@eventos_bp.route('/')
def root():
    return jsonify({
        "status": "success",
        "message": "History moments API",
        "version": "1.0"
    }), 200


@eventos_bp.route('/eventos', methods=["GET"])
def events():
    # 1. Tenta buscar da tabela 'evento' do Supabase
    try:
        res = supabase.table('evento').select('*').execute()
        if res and res.data and len(res.data) > 0:
            return jsonify(res.data), 200
    except Exception as e:
        print("Tabela 'evento' no Supabase:", e)

    # 2. Fallback para dados_eventos (periodos.json)
    return jsonify(dados_eventos), 200


@eventos_bp.route('/seed_eventos', methods=['POST', 'GET'])
def seed_eventos():
    """Rota para povoar a tabela de eventos no Supabase caso esteja vazia."""
    eventos_lista = []
    for p_idx, p in enumerate(dados_eventos):
        periodo_nome = p.get('nome', f"Período {p_idx+1}")
        for ev in p.get('acontecimentos', []):
            eventos_lista.append({
                "nome": ev.get("nome", ""),
                "periodo": periodo_nome,
                "ano_inicio": ev.get("ano", ""),
                "ano_fim": "",
                "lugar": ev.get("lugar", ""),
                "acontecimento": ev.get("oque_aconteceu", ""),
                "figuras_historicas": [f.get('nome') if isinstance(f, dict) else str(f) for f in ev.get("figuras_principais", [])],
                "imagem": ev.get("imagem", "")
            })

    sucesso = 0
    erros = []
    for ev in eventos_lista:
        try:
            supabase.table('evento').insert([ev]).execute()
            sucesso += 1
        except Exception:
            try:
                supabase.table('eventos').insert([ev]).execute()
                sucesso += 1
            except Exception as ex:
                erros.append(str(ex))

    return jsonify({
        "status": "success",
        "message": f"{sucesso} eventos sincronizados com o Supabase!",
        "erros_amostra": erros[:2]
    }), 200


@eventos_bp.route('/eventos', methods=["POST"])
def busca_por_evento():
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Insira um evento para poder receber as informações"
        }), 400

    periodo = dados.get('periodo') or dados.get('evento')
    if not periodo:
        return jsonify({
            "status": "error",
            "message": "Insira um evento ou período para poder receber as informações"
        }), 400

    try:
        # Pede para o Gemini gerar os flashcards (retorna como string JSON)
        periodo_json_string = generate_history(periodo)

        # Converte a string JSON em Dicionário Python para o Flask organizar a resposta
        informacoes_periodo = json.loads(periodo_json_string)

        return jsonify({
            "informações": informacoes_periodo
        }), 200

    except Exception as error:
        return jsonify({
            "status": "error",
            "message": f"Erro ao gerar os flashcards: {str(error)}"
        }), 500


@eventos_bp.route('/adicionar_evento', methods=['POST'])
@token_obrigatorio
def adicionar_evento():
    dados = request.get_json() or {}

    nome = dados.get("nome", "").strip()
    if not nome:
        return jsonify({
            "status": "error",
            "message": "O nome do evento é obrigatório."
        }), 400

    try:
        figuras = dados.get("figuras_historicas", [])
        if isinstance(figuras, str):
            figuras = [f.strip() for f in figuras.split(",") if f.strip()]

        dados_evento = {
            "nome": nome,
            "periodo": dados.get("periodo", "Idade Contemporânea"),
            "ano_inicio": str(dados.get("ano_inicio", dados.get("ano", ""))),
            "ano_termino": str(dados.get("ano_termino", dados.get("ano_fim", ""))),
            "lugar": str(dados.get("lugar", "")),
            "acontecimento": str(dados.get("acontecimento", dados.get("oqueAconteceu", ""))),
            "figuras_historicas": figuras,
        }

        try:
            supabase.table('evento').insert([dados_evento]).execute()
        except Exception:
            supabase.table('eventos').insert([dados_evento]).execute()

        return jsonify({
            "status": "success",
            "message": "Evento adicionado com sucesso!",
            "data": dados_evento
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao adicionar o evento: {str(e)}"
        }), 500


@eventos_bp.route('/editar_evento/<int:id>', methods=["PATCH"])
@token_obrigatorio
def editar_evento(id):
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Preencha todos os campos"
        }), 400

    campos_editaveis = ['ano_inicio', 'ano_termino', 'periodo', 'lugar', 'acontecimento', 'figuras_historicas', 'nome']

    campos_para_atualizar = {
        campo: dados[campo]
        for campo in campos_editaveis
        if campo in dados
    }
    # Compatibilidade: aceita 'ano_fim' vindo do frontend e mapeia para a coluna real 'ano_termino'
    if 'ano_fim' in dados and 'ano_termino' not in campos_para_atualizar:
        campos_para_atualizar['ano_termino'] = dados['ano_fim']

    if not campos_para_atualizar:
        return jsonify({
            "status": "error",
            "message": "Nenhum campo válido para atualizar foi fornecido"
        }), 400

    try:
        supabase.table('evento').update(campos_para_atualizar).eq('id', id).execute()

        return jsonify({
            "status": "success",
            "message": "Evento atualizado com sucesso!",
            "data": campos_para_atualizar
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao editar o evento: {str(e)}"
        }), 500


@eventos_bp.route('/deletar_evento/<int:id>', methods=["DELETE"])
@token_obrigatorio
def deletar_evento(id):
    if not id:
        return jsonify({
            "status": "error",
            "message": "ID inválido"
        }), 400
    try:
        supabase.table('evento').delete().eq('id', id).execute()
        return jsonify({
            "status": "success",
            "message": "Evento deletado com sucesso!",
            "data": id
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao deletar o evento: {str(e)}"
        }), 500
