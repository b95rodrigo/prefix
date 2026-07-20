from __future__ import annotations

import os
from pathlib import Path

from renomeador_core import (
    NOME_JOURNAL,
    NOME_LOG_OCULTO,
    RenomeadorService,
    chave_windows,
    listar_arquivos,
    montar_preview,
    nome_simples_seguro,
    salvar_json_atomico,
    validar_operacoes,
    validar_prefixo,
)


def test_validar_prefixo_rejeita_vazio_invalidos_e_controles() -> None:
    assert not validar_prefixo("   ")[0]
    assert not validar_prefixo("CLIENTE/2026")[0]
    assert not validar_prefixo("CLIENTE\nNOVO")[0]
    assert validar_prefixo("CLIENTE 2026")[0]


def test_nome_simples_seguro_bloqueia_travessia() -> None:
    assert nome_simples_seguro("arquivo.txt")
    assert not nome_simples_seguro("../arquivo.txt")
    assert not nome_simples_seguro("pasta/arquivo.txt")
    assert not nome_simples_seguro("pasta\\arquivo.txt")


def test_listar_arquivos_ignora_arquivos_internos(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "A.txt").write_text("a", encoding="utf-8")
    (tmp_path / NOME_LOG_OCULTO).write_text("{}", encoding="utf-8")
    (tmp_path / NOME_JOURNAL).write_text("{}", encoding="utf-8")
    (tmp_path / ".renomeador_prefixo_log_corrompido_20260719_120000.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "PREFIX_selftest_ok.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "subpasta").mkdir()

    assert listar_arquivos(tmp_path) == ["A.txt", "b.txt"]


def test_preview_detecta_ignorado_conflito_e_nome_longo(tmp_path: Path) -> None:
    (tmp_path / "arquivo.txt").write_text("a", encoding="utf-8")
    (tmp_path / "CLIENTE _ pronto.txt").write_text("b", encoding="utf-8")
    (tmp_path / "CLIENTE _ arquivo.txt").write_text("c", encoding="utf-8")
    nome_longo = "x" * 250 + ".txt"
    (tmp_path / nome_longo).write_text("d", encoding="utf-8")

    preview = montar_preview(
        tmp_path,
        "CLIENTE _ ",
        ["arquivo.txt", "CLIENTE _ pronto.txt", nome_longo],
    )
    por_nome = {item["atual"]: item["status"] for item in preview}
    assert por_nome["arquivo.txt"] == "conflito_existente"
    assert por_nome["CLIENTE _ pronto.txt"] == "ignorado"
    assert por_nome[nome_longo] == "nome_muito_longo"


def test_execucao_e_desfazer_completo(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.jpg").write_text("b", encoding="utf-8")
    service = RenomeadorService(tmp_path)
    operacoes, avisos = service.carregar_estado()
    assert avisos == []

    preview = montar_preview(tmp_path, "TESTE _ ", listar_arquivos(tmp_path))
    progresso: list[tuple[int, int]] = []
    resultado = service.executar(
        preview,
        "TESTE",
        operacoes,
        progresso=lambda atual, total, _nome: progresso.append((atual, total)),
    )
    assert resultado.renomeados == 2
    assert resultado.erros == []
    assert progresso[-1] == (2, 2)
    assert (tmp_path / "TESTE _ a.txt").exists()
    assert (tmp_path / "TESTE _ b.jpg").exists()
    assert (tmp_path / NOME_LOG_OCULTO).exists()
    assert not (tmp_path / NOME_JOURNAL).exists()

    operacoes, _ = service.carregar_estado()
    desfazer = service.desfazer(operacoes)
    assert desfazer.revertidos == 2
    assert desfazer.pendentes == 0
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.jpg").exists()


def test_desfazer_parcial_preserva_pendente(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    service = RenomeadorService(tmp_path)
    operacoes: list[dict[str, object]] = []
    preview = montar_preview(tmp_path, "P _ ", listar_arquivos(tmp_path))
    service.executar(preview, "P", operacoes)

    # Recria o nome original de a.txt para causar conflito apenas nesse item.
    (tmp_path / "a.txt").write_text("novo", encoding="utf-8")
    operacoes, _ = service.carregar_estado()
    resultado = service.desfazer(operacoes)

    assert resultado.revertidos == 1
    assert resultado.pendentes == 1
    assert (tmp_path / "b.txt").exists()
    assert (tmp_path / "P _ a.txt").exists()

    recarregadas, _ = service.carregar_estado()
    assert len(recarregadas) == 1
    itens = recarregadas[0]["itens"]
    assert isinstance(itens, list)
    assert itens == [{"antes": "a.txt", "depois": "P _ a.txt"}]


def test_recupera_renomeacao_interrompida(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    service = RenomeadorService(tmp_path)
    journal = {
        "versao": 1,
        "acao": "renomear",
        "id": "op-crash",
        "data_exibicao": "19/07/2026 12:00",
        "prefixo": "X",
        "itens": [{"antes": "a.txt", "depois": "X _ a.txt"}],
    }
    salvar_json_atomico(tmp_path / NOME_JOURNAL, journal)
    os.rename(tmp_path / "a.txt", tmp_path / "X _ a.txt")

    operacoes, avisos = service.carregar_estado()
    assert len(operacoes) == 1
    assert operacoes[0]["id"] == "op-crash"
    assert any("Recuperação automática" in aviso for aviso in avisos)
    assert not (tmp_path / NOME_JOURNAL).exists()


def test_recupera_desfazer_interrompido(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    service = RenomeadorService(tmp_path)
    operacoes: list[dict[str, object]] = []
    preview = montar_preview(tmp_path, "X _ ", listar_arquivos(tmp_path))
    service.executar(preview, "X", operacoes)
    operacoes, _ = service.carregar_estado()
    op = operacoes[-1]

    journal = {
        "versao": 1,
        "acao": "desfazer",
        "id": op["id"],
        "data_exibicao": op["data_exibicao"],
        "prefixo": op["prefixo"],
        "itens": op["itens"],
    }
    salvar_json_atomico(tmp_path / NOME_JOURNAL, journal)
    os.rename(tmp_path / "X _ a.txt", tmp_path / "a.txt")

    recuperadas, avisos = service.carregar_estado()
    assert len(recuperadas) == 1
    assert recuperadas[0]["itens"] == [{"antes": "b.txt", "depois": "X _ b.txt"}]
    assert any("tentativa de desfazer interrompida" in aviso for aviso in avisos)


def test_log_corrompido_e_preservado(tmp_path: Path) -> None:
    (tmp_path / NOME_LOG_OCULTO).write_text("{arquivo quebrado", encoding="utf-8")
    service = RenomeadorService(tmp_path)
    operacoes, avisos = service.carregar_estado()
    assert operacoes == []
    assert avisos
    assert list(tmp_path.glob(".renomeador_prefixo_log_corrompido_*.json"))


def test_validar_operacoes_descarta_itens_inseguros() -> None:
    dados = {
        "operacoes": [
            {
                "id": "1",
                "data_exibicao": "hoje",
                "prefixo": "X",
                "itens": [
                    {"antes": "a.txt", "depois": "X _ a.txt"},
                    {"antes": "../fora.txt", "depois": "X _ fora.txt"},
                ],
            }
        ]
    }
    operacoes = validar_operacoes(dados)
    assert operacoes[0]["itens"] == [{"antes": "a.txt", "depois": "X _ a.txt"}]


def test_chave_windows_e_case_insensitive() -> None:
    assert chave_windows("FOTO.JPG") == chave_windows("foto.jpg")
