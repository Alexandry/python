import re
import base64
from pathlib import Path
from typing import Optional, Union

# ====== Opcional: pikepdf para reparo estruturado ======
try:
    import pikepdf  # pip install pikepdf
    HAS_PIKEPDF = True
except Exception:
    HAS_PIKEPDF = False

# ====== Opcional: Pillow para reempacotar imagens ======
try:
    from PIL import Image  # pip install pillow
    HAS_PIL = True
except Exception:
    HAS_PIL = False

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

def _maybe_double_decode(b: bytes) -> bytes:
    # Se ainda for texto ASCII contendo HEX/decimal, decodifica de novo
    def _is_hex_ascii(x: bytes) -> bool:
        t = re.sub(r"\s+", "", x.decode("latin-1", errors="ignore"))
        return len(t) >= 8 and all(ch in HEX_CHARS for ch in t) and len(t) % 2 == 0
    def _is_dec_ascii(x: bytes) -> bool:
        t = x.decode("latin-1", errors="ignore")
        return bool(re.fullmatch(r"[\s,\t\r\n;0-9]+", t)) and re.search(r"\d", t)

    if _is_hex_ascii(b):
        t = re.sub(r"\s+", "", b.decode("latin-1", errors="ignore"))
        return bytes.fromhex(t)
    if _is_dec_ascii(b):
        t = b.decode("latin-1", errors="ignore")
        out = _try_parse_decimal_bytes_from_text(t)
        if out is not None:
            return out
    return b

def _trim_to_eof(pdf_bytes: bytes) -> bytes:
    idx = pdf_bytes.rfind(b"%%EOF")
    if idx == -1:
        return pdf_bytes
    end = idx + len(b"%%EOF")
    while end < len(pdf_bytes) and pdf_bytes[end:end+1] in (b"\r", b"\n"):
        end += 1
    return pdf_bytes[:end]

def _is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF-")

# ====== Extração de imagens para “data recovery” ======

def _find_jpeg_spans(b: bytes):
    # JPEG: SOI FFD8, termina em FFD9
    i = 0
    while True:
        start = b.find(b"\xFF\xD8", i)
        if start == -1:
            break
        end = b.find(b"\xFF\xD9", start + 2)
        if end == -1:
            break
        yield (start, end + 2)
        i = end + 2

def _find_png_spans(b: bytes):
    # PNG: 89504E470D0A1A0A ... termina em IEND (49 45 4E 44 AE 42 60 82)
    sig = b"\x89PNG\r\n\x1a\n"
    i = 0
    while True:
        start = b.find(sig, i)
        if start == -1:
            break
        # procura IEND
        end = b.find(b"IEND\xAE\x42\x60\x82", start + len(sig))
        if end == -1:
            break
        yield (start, end + len(b"IEND\xAE\x42\x60\x82"))
        i = end + 8

def _recover_images_to_pdf(b: bytes, out_pdf: Path) -> Optional[Path]:
    if not HAS_PIL:
        print("⚠️ Pillow não instalado; não consigo reempacotar imagens em PDF.")
        return None

    imgs = []
    for a, z in _find_jpeg_spans(b):
        imgs.append(("jpg", b[a:z]))
    for a, z in _find_png_spans(b):
        imgs.append(("png", b[a:z]))

    if not imgs:
        print("⚠️ Não encontrei imagens JPEG/PNG para recuperar.")
        return None

    # Constrói PDF com uma imagem por página
    pages = []
    for kind, data in imgs:
        from io import BytesIO
        try:
            with Image.open(BytesIO(data)) as im:
                if im.mode in ("RGBA", "P"):
                    im = im.convert("RGB")
                pages.append(im.copy())
        except Exception:
            continue

    if not pages:
        print("⚠️ Encontrei imagens, mas nenhuma pôde ser aberta pelo Pillow.")
        return None

    first, rest = pages[0], pages[1:]
    first.save(out_pdf, "PDF", save_all=True, append_images=rest)
    return out_pdf

# ====== Pipeline principal ======

def reparar_ou_recuperar_pdf_de_txt(
    diretorio: Union[str, Path],
    arquivo_txt: str,
    pdf_convertido: str = "informe_convertido.pdf",
    pdf_reparado: str = "informe_reparado.pdf",
    pdf_recuperado: str = "informe_recuperado.pdf",
) -> dict:
    dir_path = Path(diretorio)
    txt_path = dir_path / arquivo_txt
    out_conv = dir_path / pdf_convertido
    out_rep = dir_path / pdf_reparado
    out_rec = dir_path / pdf_recuperado

    if not txt_path.exists():
        raise FileNotFoundError(f"TXT não encontrado: {txt_path}")

    # 1) Decode em camadas
    raw_text = _read_text_any(txt_path)
    stage1 = _to_bytes_from_text_layer(raw_text)
    stage2 = _maybe_double_decode(stage1)
    candidate = stage2

    # 2) Alinhar em %PDF-
    pos = candidate.find(b"%PDF-")
    if pos > 0:
        candidate = candidate[pos:]

    # 3) Trim %%EOF
    trimmed = _trim_to_eof(candidate)

    # 4) Salvar o convertido (mesmo sem startxref)
    out_conv.write_bytes(trimmed)
    result = {
        "convertido": str(out_conv.resolve()),
        "reparado": None,
        "recuperado": None,
        "len_convertido": len(trimmed),
        "header": trimmed[:8].hex().upper(),
    }
    print(f"[convertido] {out_conv.name} len={len(trimmed)} header={result['header']}")

    # 5) Tentar reparo com pikepdf (se disponível)
    if HAS_PIKEPDF:
        try:
            with pikepdf.open(out_conv) as pdf:
                pdf.save(out_rep)
            result["reparado"] = str(out_rep.resolve())
            print(f"[reparado] {out_rep.name} salvo via pikepdf")
            return result
        except Exception as e:
            print(f"[pikepdf] falhou ao reparar: {e}")

    # 6) Data recovery: extrair imagens e reempacotar em PDF
    rec = _recover_images_to_pdf(trimmed, out_rec)
    if rec:
        result["recuperado"] = str(out_rec.resolve())
        print(f"[recuperado] {out_rec.name} criado com imagens extraídas.")
    else:
        print("[recuperado] não foi possível criar PDF de recuperação.")

    return result

# ===== Execução direta =====
if __name__ == "__main__":
    d = input("Diretório do TXT: ").strip()
    f = input("Nome do TXT (ex: informe.txt): ").strip()
    info = reparar_ou_recuperar_pdf_de_txt(d, f)
    print(info)
