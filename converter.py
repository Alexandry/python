import re
import base64
from pathlib import Path
from typing import Union

HEX_CHARS = set("0123456789abcdefABCDEF")

def _clean_hex(s: str) -> str:
    s = s.strip()
    if s.startswith(("0x", "0X", "\\x")):
        s = s[2:]
    # mantém apenas 0-9 A-F
    s = "".join(ch for ch in s if ch in HEX_CHARS)
    return s

def _looks_like_base64(s: str) -> bool:
    s = re.sub(r"\s+", "", s)
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s))

def _try_parse_decimal_bytes(s: str) -> bytes | None:
    tokens = re.findall(r"\d+", s)
    if not tokens:
        return None
    # se houver letras A-F, provavelmente não é decimal puro (mas pode ser misto)
    letters = re.findall(r"[A-Fa-f]", s)
    if len(letters) > 0 and len(tokens) < 10:
        return None
    vals = []
    for t in tokens:
        v = int(t)
        if not (0 <= v <= 255):
            return None
        vals.append(v)
    return bytes(vals)

def _to_bytes(raw: Union[str, bytes, bytearray]) -> bytes:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if not isinstance(raw, str):
        raise TypeError("Entrada deve ser str/bytes/bytearray.")

    s = raw.strip()

    # 1) data:*;base64,
    if s.lower().startswith("data:") and ";base64," in s:
        b64 = s.split(";base64,", 1)[1]
        return base64.b64decode(re.sub(r"\s+", "", b64))

    # 2) Base64 puro
    compact = re.sub(r"\s+", "", s)
    if _looks_like_base64(compact):
        try:
            return base64.b64decode(compact)
        except Exception:
            pass

    # 3) Decimal
    dec = _try_parse_decimal_bytes(s)
    if dec is not None:
        return dec

    # 4) HEX “sujo”
    cleaned = _clean_hex(s)
    if not cleaned:
        raise ValueError("Não foi possível interpretar como Base64, Decimal ou HEX.")
    if len(cleaned) % 2 != 0:
        # NÃO corte silenciosamente: avise (melhor do que perder 1 nibble)
        raise ValueError("Quantidade de dígitos HEX é ímpar (arquivo possivelmente truncado).")
    return bytes.fromhex(cleaned)

def _is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF-")

def _trim_to_eof(pdf_bytes: bytes) -> bytes:
    """
    Recorta o buffer até o último '%%EOF' inclusive, com tolerância a CR/LF após.
    Muitos sistemas colam bytes extras depois do PDF; leitores acusam erro.
    """
    # Procura última ocorrência de %%EOF (case exato)
    idx = pdf_bytes.rfind(b"%%EOF")
    if idx == -1:
        return pdf_bytes  # sem marcador; devolve como está (alguns PDFs antigos)
    # incluir '%%EOF'
    end = idx + len(b"%%EOF")
    # incluir \r or \n finais (opcional)
    while end < len(pdf_bytes) and pdf_bytes[end:end+1] in (b"\r", b"\n"):
        end += 1
    return pdf_bytes[:end]

def salvar_pdf_de_txt(diretorio: str, arquivo_txt: str, saida_pdf: str = "informe.pdf") -> Path:
    dir_path = Path(diretorio)
    txt_path = dir_path / arquivo_txt
    pdf_path = dir_path / saida_pdf

    if not txt_path.exists():
        raise FileNotFoundError(f"TXT não encontrado: {txt_path}")

    # Leia como texto bruto; não altere binários aqui
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")

    dados = _to_bytes(raw)

    head = dados[:8].hex().upper()
    print(f"Header (8 bytes): {head}  | Esperado p/ PDF: 255044462D ('%PDF-')")

    if not _is_pdf(dados):
        dump = pdf_path.with_suffix(".bin")
        dump.write_bytes(dados)
        raise RuntimeError(
            "Conteúdo decodificado NÃO começa com '%PDF-'. "
            f"Dump salvo em: {dump}"
        )

    # Recorta até o último %%EOF para remover lixo/trailing bytes
    dados_ok = _trim_to_eof(dados)

    # Opcional: avisa se houve recorte
    if len(dados_ok) < len(dados):
        print(f"Aviso: {len(dados) - len(dados_ok)} bytes após '%%EOF' foram descartados.")

    # Salva em binário
    pdf_path.write_bytes(dados_ok)
    print(f"✅ PDF salvo em: {pdf_path.resolve()}")
    return pdf_path

if __name__ == "__main__":
    # Exemplo: variável de input (diretório)
    diretorio_input = input("Informe o diretório onde está o arquivo TXT: ").strip()
    nome_arquivo = input("Informe o nome do arquivo TXT (ex: informe.txt): ").strip()

    try:
        salvar_informe_blob_em_pdf_de_arquivo_txt(diretorio_input, nome_arquivo, "informe_convertido.pdf")
    except Exception as e:
        print("❌ Erro:", e)
