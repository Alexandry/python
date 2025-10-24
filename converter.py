import re
import base64
from pathlib import Path
from typing import Optional, Union

# ----- Opcional (fallback imagem -> PDF) -----
try:
    from PIL import Image
    from io import BytesIO
    HAS_PIL = True
except Exception:
    HAS_PIL = False

HEX_CHARS = set("0123456789abcdefABCDEF")

def _clean_hex(s: str) -> str:
    s = s.strip()
    if s.startswith(("0x","0X","\\x")):
        s = s[2:]
    return "".join(ch for ch in s if ch in HEX_CHARS)

def _looks_like_b64(s: str) -> bool:
    s = re.sub(r"\s+", "", s)
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s)) and len(s) >= 8

def _dec_bytes_from_text(s: str) -> Optional[bytes]:
    toks = re.findall(r"\d+", s)
    if not toks:
        return None
    vals=[]
    for t in toks:
        v = int(t)
        if not (0 <= v <= 255):
            return None
        vals.append(v)
    return bytes(vals)

def to_bytes_best_effort(value: Union[bytes, bytearray, str]) -> bytes:
    """Converte retorno da coluna para bytes, aceitando bytes diretos, hex textual, base64 ou decimal."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str):
        raise TypeError(f"Tipo inesperado: {type(value)}")

    s = value.strip()

    # data:...;base64,...
    if s.lower().startswith("data:") and ";base64," in s:
        b64 = s.split(";base64,",1)[1]
        return base64.b64decode(re.sub(r"\s+","", b64))

    # base64 puro?
    compact = re.sub(r"\s+","", s)
    if _looks_like_b64(compact):
        try:
            return base64.b64decode(compact)
        except Exception:
            pass

    # decimal textual?
    dec = _dec_bytes_from_text(s)
    if dec is not None:
        return dec

    # hex textual “sujo”
    cleaned = _clean_hex(s)
    if cleaned:
        if len(cleaned) % 2 != 0:
            cleaned = cleaned[:-1]
        return bytes.fromhex(cleaned)

    raise ValueError("Valor textual não parece Base64, decimal ou HEX.")

def is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF-")

def trim_to_eof(pdf_bytes: bytes) -> bytes:
    idx = pdf_bytes.rfind(b"%%EOF")
    if idx == -1:
        return pdf_bytes
    end = idx + len(b"%%EOF")
    while end < len(pdf_bytes) and pdf_bytes[end:end+1] in (b"\r", b"\n"):
        end += 1
    return pdf_bytes[:end]

# ---- Fallback: extrair primeira imagem (JPEG/PNG) e montar PDF ----
def find_first_image_span(b: bytes):
    # JPEG
    a = b.find(b"\xFF\xD8")
    if a != -1:
        z = b.find(b"\xFF\xD9", a+2)
        if z != -1:
            return ("jpg", a, z+2)
    # PNG
    sig = b"\x89PNG\r\n\x1a\n"
    a = b.find(sig)
    if a != -1:
        z = b.find(b"IEND\xAE\x42\x60\x82", a+8)
        if z != -1:
            return ("png", a, z+8)
    return None

def image_bytes_to_single_page_pdf(img_bytes: bytes, out_pdf: Path) -> Path:
    if not HAS_PIL:
        raise RuntimeError("Pillow não instalado; não é possível montar PDF da imagem.")
    with Image.open(BytesIO(img_bytes)) as im:
        if im.mode in ("RGBA","P"):
            im = im.convert("RGB")
        im.copy().save(out_pdf, "PDF")
    return out_pdf

# ====== CONEXÃO AO SYBASE (ASE) COM PYODBC ======
def fetch_blob_from_sybase(
    dsn: Optional[str] = None,
    driver: Optional[str] = None,
    server: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    uid: Optional[str] = None,
    pwd: Optional[str] = None,
    query: str = "SELECT arquivo FROM dbo.SuaTabela WHERE id = ?",
    query_param: Optional[Union[int, str]] = None,
):
    """
    Abre conexão via ODBC (FreeTDS), executa SET TEXTSIZE e retorna o valor da 1a coluna.
    Use DSN ou DRIVER+SERVER+PORT.
    """
    if dsn:
        conn_str = f"DSN={dsn};UID={uid};PWD={pwd}"
    else:
        # Ex.: driver="FreeTDS", server="192.168.0.10", port=5000
        # Para FreeTDS, você pode ter SERVERNAME configurado no freetds.conf
        host = f"{server},{port}" if port else server
        conn_str = f"DRIVER={{{{}}}};SERVER={host};DATABASE={database};UID={uid};PWD={pwd}".format(driver)

    cn = pyodbc.connect(conn_str, autocommit=True)  # autocommit True para SET TEXTSIZE valer imediatamente
    try:
        cur = cn.cursor()
        # *** CRÍTICO NO SYBASE ASE: sem isso, TEXT/IMAGE vem truncado (ex.: ~32KB) ***
        cur.execute("SET TEXTSIZE 2147483647")
        if query_param is None:
            cur.execute(query)
        else:
            cur.execute(query, query_param)
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Nenhuma linha retornada.")
        return row[0]
    finally:
        cn.close()

def salvar_informe_da_coluna_sybase(
    saida_pdf: str,
    dsn: Optional[str] = None,
    driver: Optional[str] = None,
    server: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    uid: Optional[str] = None,
    pwd: Optional[str] = None,
    tabela: str = "dbo.SuaTabela",
    coluna: str = "arquivo",
    where: str = "id = ?",
    parametro: Optional[Union[int, str]] = None,
):
    """
    Busca o BLOB/VARBINARY/TEXT/IMAGE da coluna informada e tenta salvar:
      1) Como PDF (se começar com %PDF-)
      2) Se não for PDF, tenta extrair UMA imagem e gerar um PDF a partir dela
    """
    query = f"SELECT {coluna} FROM {tabela} WHERE {where}"
    valor = fetch_blob_from_sybase(
        dsn=dsn, driver=driver, server=server, port=port,
        database=database, uid=uid, pwd=pwd,
        query=query, query_param=parametro
    )

    # Converte retorno para bytes (casos: bytes, varbinary, nvarchar com '0x...', etc.)
    blob = to_bytes_best_effort(valor)
    print("Tamanho recebido (bytes):", len(blob))
    print("Cabeçalho (8 bytes):", blob[:8].hex().upper())

    out_path = Path(saida_pdf)

    # Caso PDF válido
    if is_pdf(blob):
        trimmed = trim_to_eof(blob)
        out_path.write_bytes(trimmed)
        print(f"PDF salvo em: {out_path.resolve()}  (len={len(trimmed)})")
        return out_path

    # Caso não seja PDF: tenta extrair imagem única e montar PDF
    span = find_first_image_span(blob)
    if span:
        kind, a, z = span
        img_bytes = blob[a:z]
        # salva imagem para conferência
        img_path = out_path.with_suffix(f".{kind}")
        img_path.write_bytes(img_bytes)
        print(f"Imagem extraída: {img_path.name}  (len={len(img_bytes)})")
        # monta PDF
        image_bytes_to_single_page_pdf(img_bytes, out_path)
        print(f"PDF (a partir da imagem) salvo em: {out_path.resolve()}")
        return out_path

    # Se chegou aqui, o conteúdo não é PDF e não tem imagem “limpa”
    dump = out_path.with_suffix(".bin")
    dump.write_bytes(blob)
    raise RuntimeError(
        "Conteúdo não é PDF e não encontrei imagem JPEG/PNG. "
        f"Dump salvo: {dump}"
    )

# ============== Exemplo de uso direto ==============
if __name__ == "__main__":
    # Escolha uma das duas formas de conexão:
    # A) Via DSN configurado no ODBC (recomendado)
    # dsn = "MinhaFonteSybase"
    # driver/server/port = None

    # B) Via DRIVER + SERVER + PORT (com FreeTDS)
    dsn = None
    driver = "FreeTDS"     # ou o nome do driver ODBC do Sybase instalado
    server = "SEU_HOST"    # IP ou hostname
    port   = 5000          # porta do ASE (comum: 5000/5001)
    database = "SUA_BASE"
    uid = "SEU_USUARIO"
    pwd = "SUA_SENHA"

    tabela = "dbo.SuaTabela"
    coluna = "arquivo"          # TEXT/IMAGE/VARBINARY
    where  = "id = ?"
    parametro = 123

    try:
        salvar_informe_da_coluna_sybase(
            saida_pdf="informe.pdf",
            dsn=dsn, driver=driver, server=server, port=port,
            database=database, uid=uid, pwd=pwd,
            tabela=tabela, coluna=coluna, where=where, parametro=parametro
        )
    except Exception as e:
        print("Erro:", e)
