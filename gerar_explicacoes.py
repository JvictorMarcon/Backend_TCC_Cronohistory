"""Gera, UMA vez, a explicação simples de cada evento e salva num arquivo estático.

O site (linha do tempo → Saber mais → "Explicar de forma simples") lê esse arquivo
direto da Vercel; ninguém chama o Gemini durante o uso. A chave só é usada aqui,
quando VOCÊ roda este script.

Uso (na pasta do backend, com o .env preenchido):
    py gerar_explicacoes.py                  # só gera os eventos que ainda não têm texto
    py gerar_explicacoes.py --refazer        # gera tudo de novo
    py gerar_explicacoes.py --saida caminho/explicacoes.json

Rode de novo sempre que cadastrar eventos novos: ele completa só o que falta.
"""
import argparse
import json
import os
import sys
import time
import unicodedata

from helpers import _gemini, dados_eventos, supabase

PADRAO_SAIDA = os.path.join(os.path.dirname(__file__), "..", "TCC-Chronohistory", "explicacoes.json")
MODELO = "gemini-3.1-flash-lite"
PAUSA_ENTRE_CHAMADAS = 4.5  # segundos: fica abaixo do limite por minuto do plano gratuito


def normalizar(texto):
    texto = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).lower().strip()


def carregar_eventos():
    """Eventos do Supabase (a fonte do site); se não der, usa o periodos.json."""
    try:
        res = supabase.table("evento").select("*").execute()
        if res and res.data:
            return [{
                "nome": e.get("nome"),
                "ano": e.get("ano_inicio") or e.get("ano") or "",
                "lugar": e.get("lugar") or "",
                "aconteceu": e.get("acontecimento") or e.get("oque_aconteceu") or "",
                "mudou": e.get("oque_mudou") or "",
            } for e in res.data if e.get("nome")], "Supabase"
    except Exception as erro:
        print("Supabase indisponível (%s); usando periodos.json" % erro)
    periodos = dados_eventos.get("periodos", []) if isinstance(dados_eventos, dict) else dados_eventos
    eventos = []
    for periodo in periodos:
        for e in periodo.get("acontecimentos", []):
            eventos.append({
                "nome": e.get("nome"), "ano": e.get("ano") or "", "lugar": e.get("lugar") or "",
                "aconteceu": e.get("oque_aconteceu") or "", "mudou": e.get("oque_mudou") or "",
            })
    return eventos, "periodos.json"


def gerar(evento):
    from google.genai import types
    prompt = f"""Explique o evento histórico abaixo para um estudante do ensino médio.
Regras: português do Brasil, linguagem simples e direta, de 3 a 4 frases em um único parágrafo, sem listas, sem markdown e sem título. Use apenas as informações fornecidas e conhecimento histórico consolidado; não invente datas, números nem citações.

Evento: {evento['nome']}
Quando: {evento['ano']}
Onde: {evento['lugar']}
O que aconteceu: {evento['aconteceu']}
O que mudou: {evento['mudou']}"""
    for tentativa in range(4):
        try:
            resposta = _gemini().models.generate_content(
                model=MODELO, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=700),
            )
            texto = (resposta.text or "").strip()
            if texto:
                return texto
        except Exception as erro:
            print("   tentativa %d falhou: %s" % (tentativa + 1, str(erro)[:120]))
        time.sleep(3 * (tentativa + 1))
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default=PADRAO_SAIDA)
    ap.add_argument("--refazer", action="store_true")
    args = ap.parse_args()

    saida = os.path.abspath(args.saida)
    existentes = {}
    if os.path.exists(saida) and not args.refazer:
        with open(saida, encoding="utf-8") as f:
            existentes = json.load(f)

    eventos, origem = carregar_eventos()
    faltando = [e for e in eventos if normalizar(e["nome"]) not in existentes]
    print("%d eventos (%s) · %d já prontos · %d para gerar" % (len(eventos), origem, len(eventos) - len(faltando), len(faltando)))

    for i, evento in enumerate(faltando, 1):
        texto = gerar(evento)
        if texto:
            existentes[normalizar(evento["nome"])] = texto
            print("[%d/%d] ok: %s" % (i, len(faltando), evento["nome"]))
        else:
            print("[%d/%d] FALHOU (rode de novo para completar): %s" % (i, len(faltando), evento["nome"]))
        # salva a cada evento: se interromper, nada se perde
        with open(saida, "w", encoding="utf-8") as f:
            json.dump(existentes, f, ensure_ascii=False, indent=0, sort_keys=True)
        time.sleep(PAUSA_ENTRE_CHAMADAS)

    print("Pronto: %d explicações em %s" % (len(existentes), saida))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
