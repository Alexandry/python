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
    # Base64 típico
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s))

def _try_parse_decimal_bytes(s: str) -> bytes | None:
    """
    Tenta interpretar o conteúdo como sequência de bytes decimais (0..255)
    separados por espaços, vírgulas ou quebras de linha.
    Retorna bytes ou None se não parecer decimal.
    """
    # pega todos os grupos de dígitos
    tokens = re.findall(r"\d+", s)
    if not tokens:
        return None

    # heurística: se praticamente só há dígitos/sep e quase nenhum A–F, assume decimal
    letters = re.findall(r"[A-Fa-f]", s)
    # se há letras hex significativas, provavelmente NÃO é decimal
    if len(letters) > 0 and len(tokens) < 10:
        return None

    try:
        vals = [int(t) for t in tokens]
    except ValueError:
        return None

    # todos no range de byte?
    if not all(0 <= v <= 255 for v in vals):
        return None

    # se tem poucos valores (ex < 10), pode ser ambíguo — mas permitido.
    return bytes(vals)

def _to_bytes(raw: Union[str, bytes, bytearray]) -> bytes:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if not isinstance(raw, str):
        raise TypeError("Entrada deve ser str/bytes/bytearray.")

    s = raw.strip()

    # 1) Data URL base64
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

    # 3) Decimal (ex.: "37 80 68 70 45 49 46...")
    dec = _try_parse_decimal_bytes(s)
    if dec is not None:
        return dec

    # 4) HEX “sujo”
    cleaned = _clean_hex(s)
    if cleaned:
        if len(cleaned) % 2 != 0:
            cleaned = cleaned[:-1]  # ajusta ímpar
        return bytes.fromhex(cleaned)

    raise ValueError("Não foi possível interpretar como Base64, Decimal ou HEX.")

def _is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF-")

def salvar_informe_blob_em_pdf_de_arquivo_txt(
    caminho_diretorio: str,
    nome_arquivo_txt: str,
    nome_pdf_saida: str = "informe.pdf",
) -> Path:
    """
    Lê um TXT com conteúdo em Base64/Decimal/HEX (com lixo),
    decodifica e salva como PDF se o conteúdo realmente for PDF.
    """
    dir_path = Path(caminho_diretorio)
    txt_path = dir_path / nome_arquivo_txt
    pdf_path = dir_path / nome_pdf_saida

    if not txt_path.exists():
        raise FileNotFoundError(f"Arquivo TXT não encontrado: {txt_path}")

    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_content = f.read()

    dados = _to_bytes(raw_content)

    # Diagnóstico rápido
    head_hex = dados[:8].hex().upper()
    print(f"Primeiros 8 bytes: {head_hex}  (esperado para PDF: 255044462D = %PDF-)")

    if _is_pdf(dados):
        pdf_path.write_bytes(dados)
        print(f"✅ PDF gerado em: {pdf_path.resolve()}")
        return pdf_path

    # não é PDF — salva dump
    dump = pdf_path.with_suffix(".bin")
    dump.write_bytes(dados)
    raise RuntimeError(
        "O conteúdo decodificado NÃO começa com '%PDF-'. "
        f"Dump salvo em: {dump}  | First bytes: {head_hex}"
    )
# =========================
# Exemplo de uso
# =========================
if __name__ == "__main__":
    # Exemplo: variável de input (diretório)
    diretorio_input = input("Informe o diretório onde está o arquivo TXT: ").strip()
    nome_arquivo = input("Informe o nome do arquivo TXT (ex: informe.txt): ").strip()

    try:
        salvar_informe_blob_em_pdf_de_arquivo_txt(diretorio_input, nome_arquivo, "informe_convertido.pdf")
    except Exception as e:
        print("❌ Erro:", e)
