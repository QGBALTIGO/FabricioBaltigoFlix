from app.providers.rastreador_pacotes import (
    RastreadorPacotesProvider,
)


def _payload():
    return {
        "codigoRastreio": "AP499229999BR",
        "nomeTransportadora": "Correios",
        "tracking": [
            {
                "CodigoRastreio": "AP499229999BR",
                "Categoria": "ENCOMENDA PAC",
                "NomeTipoCategoria": "ETIQUETA LOGICA PAC",
                "Posicoes": [
                    {
                        "Acao": "Etiqueta emitida",
                        "Data": "2026-09-15 16:33:24",
                        "Detalhes": "",
                        "DetalhesFormatado": "Etiqueta emitida\n\r",
                        "StatusPosicao": "EmTransito",
                    },
                    {
                        "Acao": "Objeto em transferência - por favor aguarde",
                        "Data": "2026-09-21 08:49:19",
                        "Detalhes": "",
                        "DetalhesFormatado": (
                            "Objeto em transferência - por favor aguarde\n\r"
                            "Saiu de Unidade de Tratamento em CURITIBA / PR "
                            "para Unidade de Tratamento em CAMPO GRANDE / MS"
                        ),
                        "StatusPosicao": "EmTransito",
                    },
                ],
            }
        ],
        "success": True,
    }


def test_rastreador_pacotes_parses_latest_correios_event():
    parsed = RastreadorPacotesProvider.parse_result(
        "AP499229999BR",
        _payload(),
    )

    latest = parsed.events[-1]

    assert parsed.provider == "rastreador_pacotes"
    assert latest.event_at.isoformat() == (
        "2026-09-21T08:49:19-03:00"
    )
    assert latest.description == (
        "Objeto em transferência - por favor aguarde"
    )
    assert latest.location == (
        "Unidade de Tratamento - CURITIBA/PR"
    )


def test_rastreador_pacotes_extracts_full_route():
    origin, destination = (
        RastreadorPacotesProvider._route_parts(
            "Objeto em transferência - por favor aguarde\n\r"
            "Saiu de Unidade de Tratamento em CURITIBA / PR "
            "para Unidade de Tratamento em CAMPO GRANDE / MS"
        )
    )

    assert origin == (
        "Unidade de Tratamento - CURITIBA/PR"
    )
    assert destination == (
        "Unidade de Tratamento - CAMPO GRANDE/MS"
    )
