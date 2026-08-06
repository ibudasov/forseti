from trading_analyst.api.schemas import TickerResponse


async def test_get_ticker_returns_contract_body(client) -> None:
    response = await client.get("/ticker/NVDA")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "symbol",
        "exchange",
        "company_name",
        "sector",
        "currency",
        "data_status",
        "data_availability",
        "tracked_since",
    }
    assert body == {
        "symbol": "NVDA",
        "exchange": "NASDAQ",
        "company_name": "NVIDIA Corporation",
        "sector": "ai",
        "currency": "USD",
        "data_status": "reference_only",
        "data_availability": {
            "price_history": False,
            "fundamentals": False,
            "news": False,
        },
        "tracked_since": "2026-08-06T12:00:00Z",
    }
    assert TickerResponse.model_validate(body)


async def test_get_ticker_is_case_insensitive(client) -> None:
    response = await client.get("/ticker/nvda")
    assert response.status_code == 200
    assert TickerResponse.model_validate(response.json()).symbol == "NVDA"


async def test_get_ticker_supports_qualified_reference(client) -> None:
    response = await client.get("/ticker/NASDAQ:NVDA")
    assert response.status_code == 200
    assert TickerResponse.model_validate(response.json()).exchange == "NASDAQ"


async def test_get_ticker_returns_not_found_for_exchange_mismatch(client) -> None:
    response = await client.get("/ticker/NYSE:NVDA")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "TICKER_NOT_FOUND",
            "message": "No ticker 'NVDA' on exchange 'NYSE' is tracked.",
        }
    }


async def test_get_ticker_returns_not_found_for_unknown_symbol(client) -> None:
    response = await client.get("/ticker/ZZZZ")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "TICKER_NOT_FOUND",
            "message": "No ticker 'ZZZZ' is tracked.",
        }
    }


async def test_get_ticker_rejects_unsupported_exchange(client) -> None:
    response = await client.get("/ticker/XETRA:SIE")
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_TICKER_REFERENCE",
            "message": "Unsupported exchange: 'XETRA'.",
        }
    }


async def test_get_ticker_rejects_invalid_symbol(client) -> None:
    response = await client.get("/ticker/NV%24DA")
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_TICKER_REFERENCE",
            "message": "'NV$DA' is not a valid ticker symbol.",
        }
    }


async def test_health_check_returns_ok(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
