import pytest

from tests.unit.test_private_facts import raw_fill


class RawExchange:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def private_get_trade_fills_history(self, params):
        self.requests.append(params.copy())
        return self.responses.pop(0)

    async def fetch_my_trades(self, *args, **kwargs):
        raise AssertionError("Unified float projections must not supply exact facts")


def adapter(exchange):
    from src.exchange.okx_private_facts import OKXSpotFillFactsAdapter

    return OKXSpotFillFactsAdapter(exchange)


def envelope(*rows, **extra):
    return {"code": "0", "msg": "", "data": list(rows)} | extra


async def test_raw_single_page_request_keeps_opaque_cursors_and_fixed_spot_type():
    exchange = RawExchange(envelope(raw_fill(billId="tail-0007")))
    page = await adapter(exchange).fetch_page(
        inst_id="BTC-USDT",
        after="older:0009",
        before="newer:0001",
        limit=37,
    )

    assert exchange.requests == [
        {
            "instType": "SPOT",
            "instId": "BTC-USDT",
            "after": "older:0009",
            "before": "newer:0001",
            "limit": "37",
        }
    ]
    assert page.fills[0].fill_px == "9007199254740993.000000000000000001"
    assert page.next_after == "tail-0007"
    assert page.coverage == "partial"
    assert page.complete is False


@pytest.mark.parametrize("rows", [[], [raw_fill()]])
async def test_short_or_empty_pages_and_provider_booleans_do_not_prove_completion(rows):
    page = await adapter(RawExchange(envelope(*rows, complete=True, hasMore=False))).fetch_page()
    assert page.complete is False
    assert page.coverage == ("partial" if rows else "unknown")
    assert page.next_after == ("009007199254740993" if rows else None)


@pytest.mark.parametrize("value", [True, 0, -1, 101, 1.0, "100", None])
async def test_invalid_limits_fail_before_calling_provider(value):
    exchange = RawExchange()
    with pytest.raises(ValueError):
        await adapter(exchange).fetch_page(limit=value)
    assert exchange.requests == []


@pytest.mark.parametrize("field", ["after", "before"])
@pytest.mark.parametrize("value", [True, 1, 1.0, "", " ", "x" * 257])
async def test_cursor_values_must_be_nonempty_bounded_opaque_strings(field, value):
    exchange = RawExchange()
    with pytest.raises(ValueError):
        await adapter(exchange).fetch_page(**{field: value})
    assert exchange.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"code": 0, "data": []},
        {"code": "50000", "data": []},
        {"code": "0", "data": None},
        {"code": "0", "data": {}},
        {"code": "0", "data": [None]},
    ],
)
async def test_bad_envelopes_and_rows_fail_closed(payload):
    with pytest.raises(ValueError):
        await adapter(RawExchange(payload)).fetch_page()


async def test_missing_bill_id_cannot_produce_usable_pagination():
    row = raw_fill()
    del row["billId"]
    with pytest.raises(ValueError):
        await adapter(RawExchange(envelope(row))).fetch_page()


@pytest.mark.parametrize(
    "field,value",
    [
        ("fillSz", "2"),
        ("fillPx", "1"),
        ("fee", "-2"),
        ("feeCcy", "USDT"),
        ("side", "sell"),
        ("ordId", "different"),
        ("tradeId", "different"),
        ("instId", "ETH-USDT"),
        ("fillTime", "1730000000002"),
    ],
)
async def test_conflicting_duplicate_bill_id_in_same_page_fails_closed(field, value):
    exchange = RawExchange(envelope(raw_fill(), raw_fill(**{field: value})))
    with pytest.raises(ValueError, match="conflicting"):
        await adapter(exchange).fetch_page()


async def test_identical_duplicate_rows_preserve_raw_observations_but_mark_partial():
    page = await adapter(RawExchange(envelope(raw_fill(), raw_fill()))).fetch_page()
    assert len(page.fills) == 2
    assert page.issues == ("duplicate_bill_id",)
    assert page.coverage == "partial"
    assert page.complete is False


async def test_conflicting_bill_id_across_prior_pages_fails_closed():
    source = adapter(RawExchange(envelope(raw_fill()), envelope(raw_fill(fee="-10"))))
    first = await source.fetch_page()
    with pytest.raises(ValueError, match="conflicting"):
        await source.fetch_page(after=first.next_after, previous_pages=(first,))


async def test_conflicts_in_supplied_prior_evidence_cannot_be_hidden_by_later_pages():
    first = await adapter(RawExchange(envelope(raw_fill(fee="-1")))).fetch_page()
    second = await adapter(RawExchange(envelope(raw_fill(fee="-2")))).fetch_page(after="start")
    exchange = RawExchange(envelope(raw_fill(billId="new")))
    with pytest.raises(ValueError, match="conflicting"):
        await adapter(exchange).fetch_page(after="next", previous_pages=(first, second))
    assert exchange.requests == []


async def test_repeated_page_fails_even_if_provider_reorders_rows():
    first_rows = [raw_fill(billId="B"), raw_fill(billId="A")]
    source = adapter(RawExchange(envelope(*first_rows), envelope(*reversed(first_rows))))
    first = await source.fetch_page()
    with pytest.raises(ValueError):
        await source.fetch_page(after=first.next_after, previous_pages=(first,))


async def test_nonadvancing_tail_cursor_fails_without_numeric_or_lexical_coercion():
    with pytest.raises(ValueError):
        await adapter(RawExchange(envelope(raw_fill(billId="opaque:0001")))).fetch_page(
            after="opaque:0001",
        )


async def test_boundary_id_anywhere_in_page_is_detected():
    with pytest.raises(ValueError):
        await adapter(
            RawExchange(envelope(raw_fill(billId="after"), raw_fill(billId="tail")))
        ).fetch_page(after="after")


async def test_longer_cursor_cycle_is_detected_from_prior_pages():
    source = adapter(
        RawExchange(
            envelope(raw_fill(billId="z")),
            envelope(raw_fill(billId="a")),
            envelope(raw_fill(billId="z")),
        )
    )
    first = await source.fetch_page()
    second = await source.fetch_page(after="z", previous_pages=(first,))
    with pytest.raises(ValueError):
        await source.fetch_page(after="a", previous_pages=(first, second))


async def test_reusing_a_request_cursor_is_detected_before_another_call():
    exchange = RawExchange(envelope(raw_fill(billId="tail")))
    source = adapter(exchange)
    first = await source.fetch_page(after="start")
    with pytest.raises(ValueError):
        await source.fetch_page(after="start", previous_pages=(first,))
    assert len(exchange.requests) == 1


async def test_overlap_with_new_tail_is_partial_and_never_complete():
    source = adapter(
        RawExchange(
            envelope(raw_fill(billId="b"), raw_fill(billId="a")),
            envelope(raw_fill(billId="b"), raw_fill(billId="c")),
        )
    )
    first = await source.fetch_page()
    second = await source.fetch_page(after="a", previous_pages=(first,))
    assert second.next_after == "c"
    assert second.issues == ("overlapping_bill_id",)
    assert second.coverage == "partial"
    assert second.complete is False


async def test_instrument_filter_mismatch_is_rejected():
    with pytest.raises(ValueError):
        await adapter(RawExchange(envelope(raw_fill(instId="ETH-USDT")))).fetch_page(
            inst_id="BTC-USDT",
        )


async def test_provider_cannot_return_more_than_requested_limit():
    with pytest.raises(ValueError):
        await adapter(RawExchange(envelope(raw_fill(), raw_fill(billId="other")))).fetch_page(
            limit=1,
        )
