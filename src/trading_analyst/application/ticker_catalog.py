from trading_analyst.domain.errors import TickerNotFoundError
from trading_analyst.domain.ticker_record import TickerRecord
from trading_analyst.domain.ticker_reference import TickerReference
from trading_analyst.domain.ticker_repository import TickerRepository


class TickerCatalog:
    def __init__(self, repository: TickerRepository) -> None:
        self._repository = repository

    async def resolve(self, reference: TickerReference) -> TickerRecord:
        record = await self._repository.find_by_symbol(reference.symbol)
        if record is None:
            raise TickerNotFoundError(
                symbol=reference.symbol.value,
                exchange=reference.exchange,
            )
        if reference.exchange is not None and record.exchange != reference.exchange:
            raise TickerNotFoundError(
                symbol=reference.symbol.value,
                exchange=reference.exchange,
            )
        return record
