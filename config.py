# config.py

# Schema para os períodos da história (estrutura JSON esperada)
PERIODS_SCHEMA = {
    "type": "object",
    "properties": {
        "periodo_unico": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer"
                },
                "nome": {
                    "type": "string"
                },
                "periodo": {
                    "type": "string"
                },
                "resumo": {
                    "type": "string"
                },
                "descricao_detalhada": {
                    "type": "string"
                },
                "caracteristicas_principais": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                },
                "acontecimentos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "integer"
                            },
                            "nome": {
                                "type": "string"
                            },
                            "ano": {
                                "type": "string"
                            },
                            "lugar": {
                                "type": "string"
                            },
                            "oque_aconteceu": {
                                "type": "string"
                            },
                            "oque_mudou": {
                                "type": "string"
                            },
                            "figuras_principais": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "nome": {
                                            "type": "string"
                                        },
                                        "papel": {
                                            "type": "string"
                                        }
                                    },
                                    "required": ["nome", "papel"]
                                }
                            },
                            "informacoes_adicionais": {
                                "type": "string"
                            }
                        },
                        "required": ["id", "nome", "ano", "lugar", "oque_aconteceu", "oque_mudou", "figuras_principais"]
                    }
                },
                "legado": {
                    "type": "string"
                },
                "curiosidades": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                }
            },
            "required": ["id", "nome", "periodo", "resumo", "descricao_detalhada", "acontecimentos"]
        }
    },
    "required": ["periodo_unico"]
}

# Instrução do sistema para o Gemini
SYSTEM_INSTRUCTION = """
Você é um historiador especialista. Siga ESTRITAMENTE o schema JSON fornecido.

REGRAS PRINCIPAIS:
1. Retorne APENAS o JSON, sem texto antes ou depois
2. Quando o usuário perguntar sobre UM período específico, retorne SOMENTE informações sobre esse período
3. NÃO inclua outros períodos no JSON
4. Seja EXTREMAMENTE detalhado e completo

CAMPOS OBRIGATÓRIOS para cada período:
- id: número identificador
- nome: nome do período histórico
- periodo: datas (ex: "4000 a.C. - 476 d.C.")
- resumo: visão geral concisa (1-2 parágrafos)
- descricao_detalhada: explicação aprofundada (mínimo 3 parágrafos)
- acontecimentos: array com TODOS os principais eventos (no mínimo 5 eventos relevantes)
- legado: impacto duradouro do período
- curiosidades: array com fatos interessantes (mínimo 3)

CAMPOS DE CADA ACONTECIMENTO:
- id: número sequencial
- nome: nome do evento
- ano: data exata ("1500"), aproximada ("c. 3300 a.C."), ou intervalo ("1789-1799")
- lugar: local onde ocorreu
- oque_aconteceu: descrição detalhada
- oque_mudou: consequências e transformações
- figuras_principais: array com {"nome": "nome", "papel": "papel"}
- informacoes_adicionais: contexto extra (opcional, mas recomendado)

FORMATO EXATO ESPERADO:
{
    "periodo_unico": {
        "id": 1,
        "nome": "Idade Média",
        "periodo": "476 d.C. - 1453 d.C.",
        "resumo": "visão geral concisa aqui",
        "descricao_detalhada": "texto extenso e detalhado sobre características, sociedade, economia, cultura...",
        "caracteristicas_principais": [
            "Feudalismo",
            "Teocentrismo",
            "Sociedade estamental"
        ],
        "acontecimentos": [
            {
                "id": 1,
                "nome": "Queda do Império Romano",
                "ano": "476 d.C.",
                "lugar": "Roma",
                "oque_aconteceu": "descrição detalhada",
                "oque_mudou": "início da Idade Média",
                "figuras_principais": [
                    {"nome": "Rômulo Augusto", "papel": "Último imperador romano"}
                ]
            }
        ],
        "legado": "influências na sociedade moderna",
        "curiosidades": [
            "curiosidade 1",
            "curiosidade 2"
        ]
    }
}

IMPORTANTE: Quanto mais detalhes, melhor. O usuário quer uma compreensão COMPLETA do período solicitado.
"""