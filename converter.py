import re
import base64
from pathlib import Path
from typing import Union

HEX_CHARS = set("0123456789abcdefABCDEF")

def _read_text_any(txt_path: Path) -> str:
    raw = txt_path.read_bytes()
    for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("latin-1", errors="ignore")

def _clean_hex(s: str) -> str:
    s = s.strip()
    if s.startswith(("0x", "0X", "\\x")):
        s = s[2:]
    return "".join(ch for ch in s if ch in HEX_CHARS)

def _looks_like_base64(s: str) -> bool:
    s = re.sub(r"\s+", "", s)
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s)) and len(s) >= 8

def _try_parse_decimal_bytes_from_text(s: str) -> Optional[bytes]:
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

def _to_bytes_from_text_layer(raw_text: str) -> bytes:
    s = raw_text.strip()

    # Data URL base64
    if s.lower().startswith("data:") and ";base64," in s:
        b64 = s.split(";base64,", 1)[1]
        return base64.b64decode(re.sub(r"\s+", "", b64))

    # Base64 puro
    compact = re.sub(r"\s+", "", s)
    if _looks_like_base64(compact):
        try:
            return base64.b64decode(compact)
        except Exception:
            pass

    # Decimal textual
    dec = _try_parse_decimal_bytes_from_text(s)
    if dec is not None:
        return dec

    # Hex “sujo”
    cleaned = _clean_hex(s)
    if cleaned:
        if len(cleaned) % 2 != 0:
            cleaned = cleaned[:-1]
        return bytes.fromhex(cleaned)

    raise ValueError("Não consegui interpretar como Base64, decimal ou hex.")

def _ascii_is_all_hex_pairs(b: bytes) -> bool:
    s = b.decode("latin-1", errors="ignore")
    s = re.sub(r"\s+", "", s)
    if len(s) < 8:
        return False
    if any(ch not in HEX_CHARS for ch in s):
        return False
    return len(s) % 2 == 0

def _ascii_is_decimal_list(b: bytes) -> bool:
    s = b.decode("latin-1", errors="ignore")
    return bool(re.search(r"\d", s)) and bool(re.fullmatch(r"[\s,\t\r\n;0-9]+", s))

def _maybe_double_decode(b: bytes) -> bytes:
    if _ascii_is_all_hex_pairs(b):
        s = re.sub(r"\s+", "", b.decode("latin-1", errors="ignore"))
        return bytes.fromhex(s)
    if _ascii_is_decimal_list(b):
        s = b.decode("latin-1", errors="ignore")
        out = _try_parse_decimal_bytes_from_text(s)
        if out is not None:
            return out
    return b

# ====== PDF helpers ======

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

def _find_last_startxref(b: bytes) -> int:
    """Retorna o índice do 'startxref' mais à direita ou -1."""
    return b.rfind(b"startxref")

def _parse_startxref_value(b: bytes, start_idx: int) -> Optional[int]:
    """
    Após 'startxref', deve haver quebra de linha e um número ASCII.
    Retorna o inteiro desse offset ou None.
    """
    if start_idx < 0:
        return None
    m = re.search(rb"startxref\s*[\r\n]+(\d+)", b[start_idx:start_idx+100], re.DOTALL)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

def _looks_like_xref_table_at(b: bytes, offset: int) -> bool:
    """Confere se em 'offset' existe 'xref' textual."""
    if 0 <= offset < len(b):
        return b[offset:offset+4] == b"xref"
    return False

def _looks_like_xref_stream_at(b: bytes, offset: int) -> bool:
    """
    Para PDFs com xref stream: 'startxref' aponta para o INÍCIO de um objeto,
    cujo conteúdo contém '/Type /XRef'. Checamos se há 'obj' e '/Type /XRef' próximo.
    """
    if not (0 <= offset < len(b)):
        return False
    # Busque algo como 'nnn n obj' nos próximos bytes
    window = b[offset: offset+2048]
    if b"obj" not in window:
        return False
    return b"/Type" in window and b"/XRef" in window

def _repair_startxref(b: bytes) -> Optional[bytes]:
    """
    Se 'startxref' estiver errado, tenta:
      - Apontar para o último 'xref' textual encontrado; ou
      - Apontar para o começo do último objeto cujo conteúdo indique '/Type /XRef'.
    Regrava o sufixo 'startxref\n<novo>\n%%EOF'.
    """
    last_sx = _find_last_startxref(b)
    if last_sx == -1:
        return None

    # Procura candidatos: último 'xref' textual
    cand_xref = b.rfind(b"xref")
    cand_xref_stream = b.rfind(b"/Type /XRef")

    target_offset = None

    if cand_xref != -1:
        # Deve apontar exatamente para o 'xre'f
        target_offset = cand_xref

    if cand_xref_stream != -1:
        # Para stream, precisamos do INÍCIO do objeto que contém '/Type /XRef'
        # Tente recuar até encontrar o padrão "<num> <gen> obj"
        start = max(0, cand_xref_stream - 200)
        snippet = b[start:cand_xref_stream+200]
        m = re.search(rb"(\d+)\s+(\d+)\s+obj", snippet)
        if m:
            # posição real do 'obj' no arquivo:
            # ajusta índice global:
            obj_local = m.start()
            obj_global = start + obj_local
            # Se não tínhamos xref textual, use o stream
            if target_offset is None:
                target_offset = obj_global

    if target_offset is None:
        return None

    # Monte novo sufixo startxref/EOF substituindo o bloco final
    # Recorte até antes de 'startxref'
    head = b[:last_sx]
    tail = f"\nstartxref\n{target_offset}\n%%EOF\n".encode("ascii")
    return head + tail

# ====== Pipeline principal ======

def converter_txt_para_pdf_com_reparo(diretorio: Union[str, Path],
                                      arquivo_txt: str,
                                      saida_pdf: str = "informe_convertido.pdf",
                                      saida_pdf_reparado: str = "informe_reparado.pdf",
                                      verbose: bool = True) -> tuple[Path, Optional[Path]]:
    dir_path = Path(diretorio)
    txt_path = dir_path / arquivo_txt
    out_pdf = dir_path / saida_pdf
    out_pdf_rep = dir_path / saida_pdf_reparado

    if not txt_path.exists():
        raise FileNotFoundError(f"TXT não encontrado: {txt_path}")

    # 1) decode em camadas
    raw_text = _read_text_any(txt_path)
    stage1 = _to_bytes_from_text_layer(raw_text)
    stage2 = _maybe_double_decode(stage1)
    candidate = stage2

    # 2) alinhar em '%PDF-' se houver ruído no começo
    pos = candidate.find(b"%PDF-")
    if pos > 0:
        if verbose:
            print(f"[ajuste] '%PDF-' encontrado em offset {pos}, recortando prefixo.")
        candidate = candidate[pos:]

    # 3) trim até %%EOF
    trimmed = _trim_to_eof(candidate)

    # 4) salvar o “convertido”
    out_pdf.write_bytes(trimmed)
    if verbose:
        print(f"[salvo] {out_pdf.name} len={len(trimmed)} head={trimmed[:8].hex().upper()}")

    # 5) validar startxref
    last_sx = _find_last_startxref(trimmed)
    sx_val = _parse_startxref_value(trimmed, last_sx) if last_sx != -1 else None
    ok = False
    reason = ""

    if last_sx == -1 or sx_val is None:
        reason = "startxref ausente ou ilegível"
    else:
        if _looks_like_xref_table_at(trimmed, sx_val) or _looks_like_xref_stream_at(trimmed, sx_val):
            ok = True
        else:
            reason = f"startxref aponta para offset {sx_val}, que não parece xref nem xref stream."

    if ok:
        if verbose:
            print("[ok] startxref válido; tente abrir o informe_convertido.pdf.")
        return out_pdf, None

    # 6) tentar reparo de startxref
    fixed = _repair_startxref(trimmed)
    if not fixed:
        if verbose:
            print(f"[falha-reparo] Não consegui ajustar startxref ({reason}).")
        return out_pdf, None

    out_pdf_rep.write_bytes(fixed)
    if verbose:
        new_last_sx = _find_last_startxref(fixed)
        new_sx_val = _parse_startxref_value(fixed, new_last_sx) if new_last_sx != -1 else None
        print(f"[reparo] Gerado {out_pdf_rep.name} len={len(fixed)} "
              f"startxref={new_sx_val}")
    return out_pdf, out_pdf_rep

# ====== Execução ======
# ============== Execução direta ==============
if __name__ == "__main__":
    # Exemplo: variável de input (diretório)
    diretorio_input = input("Informe o diretório onde está o arquivo TXT: ").strip()
    nome_arquivo = input("Informe o nome do arquivo TXT (ex: informe.txt): ").strip()

    try:
        path_pdf, path_pdf_reparado = converter_txt_para_pdf_com_reparo(d, f)
        print("✅ Arquivo principal:", path_pdf.resolve())
        if path_pdf_reparado:
            print("🩹 Arquivo reparado:", path_pdf_reparado.resolve())
    except Exception as e:
        print("❌ Erro:", e)
