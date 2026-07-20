from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

NOME_LOG_OCULTO = ".renomeador_prefixo_log.json"
NOME_JOURNAL = ".renomeador_prefixo_journal.json"
MAX_OPERACOES_LOG = 5
MAX_NOME_UTF16 = 255
CARACTERES_INVALIDOS = set('\\/:*?"<>|')
ARQUIVOS_INTERNOS = {
    NOME_LOG_OCULTO,
    NOME_JOURNAL,
    "PREFIX_selftest_error.txt",
    "PREFIX_selftest_ok.txt",
}
PREFIXOS_INTERNOS = {f"{Path(NOME_LOG_OCULTO).stem}_corrompido_"}

ProgressCallback = Callable[[int, int, str], None]


class PersistenciaErro(RuntimeError):
    """Erro ao salvar dados de segurança da operação."""


@dataclass(slots=True)
class ResultadoExecucao:
    renomeados: int = 0
    ignorados: int = 0
    conflitos: int = 0
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResultadoDesfazer:
    revertidos: int = 0
    pendentes: int = 0
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def chave_windows(nome: str) -> str:
    """Normaliza um nome com semântica próxima à comparação do Windows/NTFS."""
    return unicodedata.normalize("NFC", nome).casefold()


def comprimento_utf16(texto: str) -> int:
    return len(texto.encode("utf-16-le")) // 2


def nome_simples_seguro(nome: object) -> bool:
    if not isinstance(nome, str) or not nome or nome in {".", ".."}:
        return False
    if "\x00" in nome or any(ord(ch) < 32 for ch in nome):
        return False
    if "/" in nome or "\\" in nome:
        return False
    return Path(nome).name == nome


def validar_prefixo(prefixo: str) -> tuple[bool, str]:
    prefixo = prefixo.strip()
    if not prefixo:
        return False, "Digite um prefixo antes de continuar."

    invalidos = sorted({ch for ch in prefixo if ch in CARACTERES_INVALIDOS})
    if invalidos:
        return False, (
            "O prefixo contém caracteres não permitidos em nomes de arquivo:\n"
            + " ".join(invalidos)
        )

    controles = sorted({f"U+{ord(ch):04X}" for ch in prefixo if ord(ch) < 32})
    if controles:
        return False, "O prefixo contém caracteres de controle inválidos: " + ", ".join(controles)

    if comprimento_utf16(prefixo) > 180:
        return False, "O prefixo é muito longo. Use até 180 caracteres."

    return True, ""


def _eh_arquivo_interno(nome: str) -> bool:
    if nome in ARQUIVOS_INTERNOS:
        return True
    if any(nome.startswith(prefixo) for prefixo in PREFIXOS_INTERNOS):
        return True
    return any(nome.startswith(f"{interno}.tmp-") for interno in ARQUIVOS_INTERNOS)


def listar_arquivos(pasta: str | os.PathLike[str], executavel_atual: str | None = None) -> list[str]:
    caminho_pasta = Path(pasta)
    if not caminho_pasta.exists():
        raise FileNotFoundError(f"A pasta não existe: {caminho_pasta}")
    if not caminho_pasta.is_dir():
        raise NotADirectoryError(f"O caminho selecionado não é uma pasta: {caminho_pasta}")

    atual_resolvido: Path | None = None
    if executavel_atual:
        try:
            atual_resolvido = Path(executavel_atual).resolve()
        except OSError:
            atual_resolvido = None

    arquivos: list[str] = []
    with os.scandir(caminho_pasta) as it:
        for entry in it:
            if not entry.is_file(follow_symlinks=False):
                continue
            if _eh_arquivo_interno(entry.name):
                continue
            if atual_resolvido is not None:
                try:
                    if Path(entry.path).resolve() == atual_resolvido:
                        continue
                except OSError:
                    pass
            arquivos.append(entry.name)

    return sorted(arquivos, key=chave_windows)


def montar_preview(
    pasta: str | os.PathLike[str],
    prefixo_formatado: str,
    arquivos: Iterable[str],
) -> list[dict[str, str]]:
    pasta_path = Path(pasta)
    with os.scandir(pasta_path) as it:
        existentes = {chave_windows(entry.name) for entry in it if entry.is_file(follow_symlinks=False)}

    resultados: list[dict[str, str]] = []
    contagem_destinos: dict[str, int] = {}
    prefixo_key = chave_windows(prefixo_formatado)

    for nome in sorted(arquivos, key=chave_windows):
        base, ext = os.path.splitext(nome)
        if chave_windows(base).startswith(prefixo_key):
            resultados.append({"atual": nome, "novo": nome, "status": "ignorado"})
            continue

        novo_nome = f"{prefixo_formatado}{base}{ext}"
        status = "renomear"
        if comprimento_utf16(novo_nome) > MAX_NOME_UTF16:
            status = "nome_muito_longo"

        resultados.append({"atual": nome, "novo": novo_nome, "status": status})
        if status == "renomear":
            destino_key = chave_windows(novo_nome)
            contagem_destinos[destino_key] = contagem_destinos.get(destino_key, 0) + 1

    for item in resultados:
        if item["status"] != "renomear":
            continue
        atual_key = chave_windows(item["atual"])
        destino_key = chave_windows(item["novo"])
        if contagem_destinos[destino_key] > 1:
            item["status"] = "conflito_duplicado"
        elif destino_key in existentes and destino_key != atual_key:
            item["status"] = "conflito_existente"

    return resultados


def _ocultar_no_windows(caminho: Path) -> None:
    if os.name != "nt":
        return
    try:
        get_attrs = ctypes.windll.kernel32.GetFileAttributesW
        set_attrs = ctypes.windll.kernel32.SetFileAttributesW
        attrs = get_attrs(str(caminho))
        if attrs != 0xFFFFFFFF:
            set_attrs(str(caminho), attrs | 0x02)
    except Exception:
        pass


def salvar_json_atomico(caminho: Path, dados: object, ocultar: bool = False) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f"{caminho.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=2)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        if ocultar:
            _ocultar_no_windows(caminho)
    except Exception as exc:
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass
        raise PersistenciaErro(f"Não foi possível salvar '{caminho.name}': {exc}") from exc


def _validar_itens(itens: object) -> list[dict[str, str]]:
    if not isinstance(itens, list):
        return []
    validos: list[dict[str, str]] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        antes = item.get("antes")
        depois = item.get("depois")
        if nome_simples_seguro(antes) and nome_simples_seguro(depois):
            validos.append({"antes": antes, "depois": depois})
    return validos


def validar_operacoes(dados: object) -> list[dict[str, object]]:
    if not isinstance(dados, dict):
        return []
    operacoes = dados.get("operacoes")
    if not isinstance(operacoes, list):
        return []

    validas: list[dict[str, object]] = []
    for operacao in operacoes[-MAX_OPERACOES_LOG:]:
        if not isinstance(operacao, dict):
            continue
        itens = _validar_itens(operacao.get("itens"))
        if not itens:
            continue
        validas.append(
            {
                "id": str(operacao.get("id") or uuid.uuid4().hex),
                "data_exibicao": str(operacao.get("data_exibicao") or "Data desconhecida"),
                "prefixo": str(operacao.get("prefixo") or ""),
                "itens": itens,
            }
        )
    return validas


class RenomeadorService:
    def __init__(self, pasta: str | os.PathLike[str]):
        self.pasta = Path(pasta)
        self.log_path = self.pasta / NOME_LOG_OCULTO
        self.journal_path = self.pasta / NOME_JOURNAL

    def carregar_estado(self) -> tuple[list[dict[str, object]], list[str]]:
        avisos: list[str] = []
        operacoes: list[dict[str, object]] = []

        if self.log_path.exists():
            try:
                with self.log_path.open("r", encoding="utf-8") as arquivo:
                    bruto = json.load(arquivo)
                operacoes = validar_operacoes(bruto)
                if bruto.get("operacoes") and not operacoes:
                    avisos.append("O histórico de desfazer estava inválido e foi ignorado por segurança.")
            except Exception as exc:
                backup = self.log_path.with_name(
                    f"{self.log_path.stem}_corrompido_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
                )
                try:
                    os.replace(self.log_path, backup)
                    avisos.append(
                        f"O histórico estava corrompido e foi preservado como '{backup.name}'."
                    )
                except OSError:
                    avisos.append(f"Não foi possível ler o histórico de desfazer: {exc}")

        if self.journal_path.exists():
            operacoes, avisos_recuperacao = self._recuperar_journal(operacoes)
            avisos.extend(avisos_recuperacao)

        return operacoes[-MAX_OPERACOES_LOG:], avisos

    def salvar_operacoes(self, operacoes: Sequence[dict[str, object]]) -> None:
        salvar_json_atomico(
            self.log_path,
            {"versao": 2, "operacoes": list(operacoes)[-MAX_OPERACOES_LOG:]},
            ocultar=True,
        )

    def _mapa_nomes(self) -> dict[str, str]:
        with os.scandir(self.pasta) as it:
            return {
                chave_windows(entry.name): entry.name
                for entry in it
                if entry.is_file(follow_symlinks=False)
            }

    def _carregar_journal(self) -> dict[str, object] | None:
        try:
            with self.journal_path.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except Exception:
            return None
        if not isinstance(dados, dict):
            return None
        acao = dados.get("acao")
        if acao not in {"renomear", "desfazer"}:
            return None
        itens = _validar_itens(dados.get("itens"))
        if not itens:
            return None
        return {
            "acao": acao,
            "id": str(dados.get("id") or uuid.uuid4().hex),
            "data_exibicao": str(dados.get("data_exibicao") or "Data desconhecida"),
            "prefixo": str(dados.get("prefixo") or ""),
            "itens": itens,
        }

    def _recuperar_journal(
        self, operacoes: list[dict[str, object]]
    ) -> tuple[list[dict[str, object]], list[str]]:
        avisos: list[str] = []
        journal = self._carregar_journal()
        if journal is None:
            avisos.append(
                "Foi encontrado um diário de segurança inválido; "
                "ele foi mantido para análise manual."
            )
            return operacoes, avisos

        mapa = self._mapa_nomes()
        itens = list(journal["itens"])
        acao = str(journal["acao"])
        operacao_id = str(journal["id"])

        if acao == "renomear":
            ids_existentes = {str(op.get("id")) for op in operacoes}
            recuperados: list[dict[str, str]] = []
            ambiguos = 0
            for item in itens:
                antes_existe = chave_windows(item["antes"]) in mapa
                depois_existe = chave_windows(item["depois"]) in mapa
                if depois_existe and not antes_existe:
                    recuperados.append(item)
                elif antes_existe and not depois_existe:
                    continue
                else:
                    ambiguos += 1

            if recuperados and operacao_id not in ids_existentes:
                operacoes.append(
                    {
                        "id": operacao_id,
                        "data_exibicao": journal["data_exibicao"],
                        "prefixo": journal["prefixo"],
                        "itens": recuperados,
                    }
                )
                avisos.append(
                    "Recuperação automática: "
                    f"{len(recuperados)} arquivo(s) renomeado(s) voltaram "
                    "ao histórico de desfazer."
                )
            if ambiguos:
                avisos.append(
                    f"{ambiguos} item(ns) do processo interrompido ficaram ambíguos; "
                    "confira os nomes na pasta."
                )

        else:  # desfazer
            indice = next(
                (i for i, op in enumerate(operacoes) if str(op.get("id")) == operacao_id),
                None,
            )
            if indice is not None:
                pendentes: list[dict[str, str]] = []
                ambiguos = 0
                for item in itens:
                    antes_existe = chave_windows(item["antes"]) in mapa
                    depois_existe = chave_windows(item["depois"]) in mapa
                    if depois_existe and not antes_existe:
                        pendentes.append(item)
                    elif antes_existe and not depois_existe:
                        continue
                    else:
                        pendentes.append(item)
                        ambiguos += 1
                if pendentes:
                    operacoes[indice]["itens"] = pendentes
                else:
                    operacoes.pop(indice)
                avisos.append("O histórico foi ajustado após uma tentativa de desfazer interrompida.")
                if ambiguos:
                    avisos.append(f"{ambiguos} item(ns) ainda precisam de conferência manual.")

        try:
            self.salvar_operacoes(operacoes)
            self.journal_path.unlink(missing_ok=True)
        except Exception as exc:
            avisos.append(f"A recuperação foi identificada, mas não pôde ser persistida: {exc}")

        return operacoes[-MAX_OPERACOES_LOG:], avisos

    def executar(
        self,
        resultados: Sequence[dict[str, str]],
        prefixo: str,
        operacoes: list[dict[str, object]],
        progresso: ProgressCallback | None = None,
    ) -> ResultadoExecucao:
        resultado = ResultadoExecucao(
            ignorados=sum(1 for item in resultados if item["status"] == "ignorado"),
            conflitos=sum(1 for item in resultados if item["status"] not in {"renomear", "ignorado"}),
        )
        a_renomear = [item for item in resultados if item["status"] == "renomear"]
        if not a_renomear:
            return resultado

        operacao_id = uuid.uuid4().hex
        data_exibicao = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
        planejados = [
            {"antes": item["atual"], "depois": item["novo"]}
            for item in a_renomear
            if nome_simples_seguro(item["atual"]) and nome_simples_seguro(item["novo"])
        ]
        journal = {
            "versao": 1,
            "acao": "renomear",
            "id": operacao_id,
            "data_exibicao": data_exibicao,
            "prefixo": prefixo,
            "itens": planejados,
        }
        salvar_json_atomico(self.journal_path, journal, ocultar=True)

        mapa = self._mapa_nomes()
        concluidos: list[dict[str, str]] = []
        total = len(planejados)

        for indice, item in enumerate(planejados, start=1):
            antes_key = chave_windows(item["antes"])
            depois_key = chave_windows(item["depois"])
            nome_real = mapa.get(antes_key)
            if nome_real is None:
                resultado.erros.append(f"Arquivo de origem não encontrado: {item['antes']}")
            elif depois_key in mapa and depois_key != antes_key:
                resultado.erros.append(f"O destino passou a existir durante a operação: {item['depois']}")
            else:
                try:
                    os.rename(self.pasta / nome_real, self.pasta / item["depois"])
                    mapa.pop(antes_key, None)
                    mapa[depois_key] = item["depois"]
                    concluidos.append(item)
                    resultado.renomeados += 1
                except Exception as exc:
                    resultado.erros.append(f"Erro ao renomear '{item['antes']}': {exc}")

            if progresso:
                progresso(indice, total, item["antes"])

        if concluidos:
            operacoes.append(
                {
                    "id": operacao_id,
                    "data_exibicao": data_exibicao,
                    "prefixo": prefixo,
                    "itens": concluidos,
                }
            )
            try:
                self.salvar_operacoes(operacoes)
                self.journal_path.unlink(missing_ok=True)
            except Exception as exc:
                resultado.avisos.append(
                    "Os arquivos foram renomeados, mas o histórico não pôde ser finalizado. "
                    f"O diário de recuperação foi mantido. Detalhe: {exc}"
                )
        else:
            self.journal_path.unlink(missing_ok=True)

        return resultado

    def desfazer(
        self,
        operacoes: list[dict[str, object]],
        progresso: ProgressCallback | None = None,
    ) -> ResultadoDesfazer:
        if not operacoes:
            return ResultadoDesfazer()

        operacao = operacoes[-1]
        itens = _validar_itens(operacao.get("itens"))
        if not itens:
            operacoes.pop()
            self.salvar_operacoes(operacoes)
            return ResultadoDesfazer()

        journal = {
            "versao": 1,
            "acao": "desfazer",
            "id": str(operacao.get("id")),
            "data_exibicao": operacao.get("data_exibicao", ""),
            "prefixo": operacao.get("prefixo", ""),
            "itens": itens,
        }
        salvar_json_atomico(self.journal_path, journal, ocultar=True)

        mapa = self._mapa_nomes()
        pendentes: list[dict[str, str]] = []
        resultado = ResultadoDesfazer()
        total = len(itens)

        for indice, item in enumerate(reversed(itens), start=1):
            antes_key = chave_windows(item["antes"])
            depois_key = chave_windows(item["depois"])
            nome_real = mapa.get(depois_key)

            if nome_real is None:
                pendentes.append(item)
                resultado.erros.append(f"Arquivo renomeado não encontrado: {item['depois']}")
            elif antes_key in mapa and antes_key != depois_key:
                pendentes.append(item)
                resultado.erros.append(
                    f"Não foi possível restaurar porque o nome original já existe: {item['antes']}"
                )
            else:
                try:
                    os.rename(self.pasta / nome_real, self.pasta / item["antes"])
                    mapa.pop(depois_key, None)
                    mapa[antes_key] = item["antes"]
                    resultado.revertidos += 1
                except Exception as exc:
                    pendentes.append(item)
                    resultado.erros.append(f"Erro ao restaurar '{item['depois']}': {exc}")

            if progresso:
                progresso(indice, total, item["depois"])

        pendentes.reverse()
        resultado.pendentes = len(pendentes)
        if pendentes:
            operacao["itens"] = pendentes
        else:
            operacoes.pop()

        try:
            self.salvar_operacoes(operacoes)
            self.journal_path.unlink(missing_ok=True)
        except Exception as exc:
            resultado.avisos.append(
                "O desfazer foi executado, mas o histórico não pôde ser finalizado. "
                f"O diário de recuperação foi mantido. Detalhe: {exc}"
            )

        return resultado


def executar_autoteste_core() -> None:
    with tempfile.TemporaryDirectory(prefix="prefix_autoteste_") as pasta:
        base = Path(pasta)
        (base / "arquivo.txt").write_text("ok", encoding="utf-8")
        (base / "foto.JPG").write_bytes(b"imagem")
        (base / "CLIENTE _ pronto.txt").write_text("ok", encoding="utf-8")

        service = RenomeadorService(base)
        arquivos = listar_arquivos(base)
        preview = montar_preview(base, "CLIENTE _ ", arquivos)
        assert sum(1 for item in preview if item["status"] == "renomear") == 2
        assert sum(1 for item in preview if item["status"] == "ignorado") == 1

        operacoes, avisos = service.carregar_estado()
        assert not avisos
        resultado = service.executar(preview, "CLIENTE", operacoes)
        assert resultado.renomeados == 2
        assert (base / "CLIENTE _ arquivo.txt").exists()
        assert (base / "CLIENTE _ foto.JPG").exists()

        operacoes, _ = service.carregar_estado()
        assert len(operacoes) == 1
        desfazer = service.desfazer(operacoes)
        assert desfazer.revertidos == 2
        assert desfazer.pendentes == 0
        assert (base / "arquivo.txt").exists()
        assert (base / "foto.JPG").exists()
