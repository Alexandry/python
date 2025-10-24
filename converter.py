import re
import base64
from pathlib import Path
from typing import Union

HEX_CHARS = set("0123456789abcdefABCDEF")

def _clean_hex(s: str) -> str:
    # Remove prefixos comuns e TUDO que não for [0-9A-F]
    s = s.strip()
    if s.startswith(("0x", "0X", "\\x")):
        s = s[2:]
    # remove todos os caracteres que não fazem parte de um número HEX válido
    s = "".join(ch for ch in s if ch in HEX_CHARS)
    return s

def _looks_like_base64(s: str) -> bool:
    s = re.sub(r"\s+", "", s)
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s))

def _to_bytes(raw: Union[str, bytes, bytearray]) -> bytes:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if not isinstance(raw, str):
        raise TypeError("Entrada deve ser str/bytes/bytearray.")

    s = raw.strip()

    # Detectar Base64 direto
    compact = re.sub(r"\s+", "", s)
    if _looks_like_base64(compact):
        try:
            return base64.b64decode(compact)
        except Exception:
            pass

    # Tratar como HEX "sujo"
    cleaned = _clean_hex(s)
    if len(cleaned) == 0:
        raise ValueError("Nenhum conteúdo HEX/Base64 válido após limpeza.")
    if len(cleaned) % 2 != 0:
        cleaned = cleaned[:-1]  # ajustar número ímpar
    
    return bytes.fromhex(cleaned)

def _is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF-")

def salvar_informe_blob_em_pdf_de_arquivo_txt(caminho_diretorio: str, nome_arquivo_txt: str, nome_pdf_saida: str = "informe.pdf") -> Path:
    """
    Lê um arquivo TXT com conteúdo decimal/hex misturado de um diretório,
    converte para PDF e salva no mesmo diretório.
    """
    dir_path = Path(caminho_diretorio)
    txt_path = dir_path / nome_arquivo_txt
    pdf_path = dir_path / nome_pdf_saida

    if not txt_path.exists():
        raise FileNotFoundError(f"Arquivo TXT não encontrado: {txt_path}")

    # Ler conteúdo do TXT
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_content = f.read()

    # Converter para bytes
    dados = _to_bytes(raw_content)

    # Validar e salvar PDF
    if _is_pdf(dados):
        pdf_path.write_bytes(dados)
        print(f"✅ PDF gerado com sucesso em: {pdf_path.resolve()}")
        return pdf_path
    else:
        dump = pdf_path.with_suffix(".bin")
        dump.write_bytes(dados)
        head = dados[:32].hex().upper()
        raise RuntimeError(
            "O conteúdo decodificado não parece ser um PDF válido (%PDF-).\n"
            f"Primeiros 32 bytes: {head}\n"
            f"Bytes salvos para análise em: {dump}"
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
