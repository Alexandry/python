import re
import base64
from pathlib import Path
from typing import Union

HEX_CHARS = set("0123456789abcdefABCDEF")

def _read_text_any(txt_path: Path) -> str:
    # Lê bytes e tenta decodificar por encodings comuns de dumps
    raw = txt_path.read_bytes()
    for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    # último recurso: latin-1 com ignore
    return raw.decode("latin-1", errors="ignore")

def _save_debug(path: Path, name: str, content: bytes):
    (path.parent / (path.name + f".{name}.bin")).write_bytes(content)

# ============== Heurísticas de detecção ==============

HEX_CHARS = set("0123456789abcdefABCDEF")

def _clean_hex(s: str) -> str:
    s = s.strip()
    if s.startswith(("0x", "0X", "\\x")):
        s = s[2:]
    return "".join(ch for ch in s if ch in HEX_CHARS)

def _looks_like_base64(s: str) -> bool:
    s = re.sub(r"\s+", "", s)
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s)) and len(s) >= 8

def _try_parse_decimal_bytes_from_text(s: str) -> bytes | None:
    # Aceita 0..255 separados por espaço, vírgula, ponto e vírgula, tabs ou quebras
    tokens = re.findall(r"\d+", s)
    if not tokens:
        return None
    vals = []
    for t in tokens:
        v = int(t)
        if not (0 <= v <= 255):
            return None
        vals.append(v)
    return bytes(vals)

def _ascii_is_all_hex_pairs(b: bytes) -> bool:
    # Ex.: b"255044462D312E34..." => só dígitos hex e possivelmente espaços/novas linhas
    s = b.decode("latin-1", errors="ignore")
    s = re.sub(r"\s+", "", s)
    if len(s) < 8:
        return False
    if any(ch not in HEX_CHARS for ch in s):
        return False
    return len(s) % 2 == 0

def _ascii_is_decimal_list(b: bytes) -> bool:
    # Ex.: b"37 80 68 70 ..." (decimais) ou b"50 53 50 44 46 2D ..." (decimais de ASCII "25504446-")
    s = b.decode("latin-1", errors="ignore")
    return bool(re.search(r"\d", s)) and bool(re.fullmatch(r"[\s,\t\r\n;0-9]+", s))

def _to_bytes_from_text_layer(raw_text: str) -> bytes:
    """
    1) data:*;base64,
    2) Base64 puro
    3) Decimal textual
    4) Hex “sujo”
    """
    s = raw_text.strip()

    # 1) data URL
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
    dec = _try_parse_decimal_bytes_from_text(s)
    if dec is not None:
        return dec

    # 4) Hex “sujo”
    cleaned = _clean_hex(s)
    if cleaned:
        if len(cleaned) % 2 != 0:
            # não corte silenciosamente — melhor avisar
            cleaned = cleaned[:-1]
        return bytes.fromhex(cleaned)

    raise ValueError("Não consegui interpretar como Base64, decimal ou hex.")

def _maybe_double_decode(b: bytes) -> bytes:
    """
    Se ainda for texto ASCII contendo HEX/decimal, tenta decodificar de novo.
    """
    # ASCII-HEX?
    if _ascii_is_all_hex_pairs(b):
        s = re.sub(r"\s+", "", b.decode("latin-1", errors="ignore"))
        return bytes.fromhex(s)
    # ASCII-DECIMAL?
    if _ascii_is_decimal_list(b):
        s = b.decode("latin-1", errors="ignore")
        out = _try_parse_decimal_bytes_from_text(s)
        if out is not None:
            return out
    return b

# ============== PDF helpers ==============

def _is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF-")

def _trim_to_eof(pdf_bytes: bytes) -> bytes:
    idx = pdf_bytes.rfind(b"%%EOF")
    if idx == -1:
        return pdf_bytes
    end = idx + len(b"%%EOF")
    while end < len(pdf_bytes) and pdf_bytes[end:end+1] in (b"\r", b"\n"):
        end += 1
    return pdf_bytes[:end]

# ============== Função principal ==============

def salvar_pdf_de_txt(
    diretorio: Union[str, Path],
    arquivo_txt: str,
    saida_pdf: str = "informe.pdf",
    verbose: bool = True,
) -> Path:
    dir_path = Path(diretorio)
    txt_path = dir_path / arquivo_txt
    pdf_path = dir_path / saida_pdf

    if not txt_path.exists():
        raise FileNotFoundError(f"TXT não encontrado: {txt_path}")

    # 1) Ler como texto (tentando vários encodings)
    raw_text = _read_text_any(txt_path)
    (dir_path / (pdf_path.name + ".raw.txt")).write_text(raw_text, encoding="utf-8", errors="ignore")

    # 2) Primeira camada de decodificação
    stage1 = _to_bytes_from_text_layer(raw_text)
    if verbose:
        print(f"[stage1] head: {stage1[:16].hex().upper()}  len={len(stage1)}")
    _save_debug(pdf_path, "stage1", stage1)

    # 3) Se ainda parecer texto HEX/decimal, decodifica de novo
    stage2 = _maybe_double_decode(stage1)
    if stage2 is not stage1:
        if verbose:
            print(f"[stage2] head: {stage2[:16].hex().upper()}  len={len(stage2)} (dupla decodificação)")
        _save_debug(pdf_path, "stage2", stage2)
    else:
        if verbose:
            print("[stage2] não foi necessário (não pareceu texto-HEX/decimal).")

    # 4) Verificar header PDF
    candidate = stage2
    if not _is_pdf(candidate):
        # última tentativa: às vezes há BOM/texto antes do %PDF-
        # procura a primeira ocorrência de b'%PDF-'
        pos = candidate.find(b"%PDF-")
        if pos > 0:
            if verbose:
                print(f"[ajuste] encontrado '%PDF-' em offset {pos}, recortando prefixo ruidoso.")
            candidate = candidate[pos:]
        else:
            _save_debug(pdf_path, "not_pdf", candidate)
            raise RuntimeError(
                "Conteúdo final não começa com '%PDF-'. "
                f"head={candidate[:16].hex().upper()}  len={len(candidate)}"
            )

    # 5) Cortar até o último %%EOF
    trimmed = _trim_to_eof(candidate)
    if len(trimmed) < len(candidate) and verbose:
        print(f"[trim] removidos {len(candidate) - len(trimmed)} bytes após '%%EOF'.")

    # 6) Salvar
    pdf_path.write_bytes(trimmed)
    if verbose:
        print(f"✅ PDF salvo em: {pdf_path.resolve()}  (len={len(trimmed)})")
    return pdf_path

# ============== Execução direta ==============
if __name__ == "__main__":
    # Exemplo: variável de input (diretório)
    diretorio_input = input("Informe o diretório onde está o arquivo TXT: ").strip()
    nome_arquivo = input("Informe o nome do arquivo TXT (ex: informe.txt): ").strip()

    try:
        salvar_informe_blob_em_pdf_de_arquivo_txt(diretorio_input, nome_arquivo, "informe_convertido.pdf")
    except Exception as e:
        print("❌ Erro:", e)
