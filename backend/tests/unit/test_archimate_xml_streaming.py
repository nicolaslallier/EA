"""The reader is a stream: memory follows the depth, and a bound stops it where it is crossed."""

from __future__ import annotations

import tracemalloc
from collections.abc import Callable, Iterator
from typing import Any

import pytest

import ea.formats.archimate_xml as reader
from ea.domain.errors import InvalidExchangeFileError
from ea.formats.archimate_xml import parse_model
from tests.exchange_files import el, exchange_xml


class Spy:
    """Stands in for `iterparse`: counts the events handed out and how big the live tree is."""

    def __init__(self, real: Callable[..., Iterator[tuple[str, Any]]]) -> None:
        self._real = real
        self.events = 0
        self.live_nodes = 0  # the most nodes the tree ever held at a sampled moment

    def __call__(self, *args: Any, **kwargs: Any) -> Iterator[tuple[str, Any]]:
        depth = 0
        section = None  # the node holding the items: where a tree that is not forgotten grows
        for event, node in self._real(*args, **kwargs):
            self.events += 1
            if event == "start":
                depth += 1
                if depth == 2 and node.tag.endswith("}elements"):
                    section = node
            elif event == "end":
                depth -= 1
            if section is not None:  # its direct children: O(1), so it can be asked at every event
                self.live_nodes = max(self.live_nodes, len(section))
            yield event, node


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    watching = Spy(reader.iterparse)
    monkeypatch.setattr(reader, "iterparse", watching)
    return watching


def many(count: int) -> bytes:
    return exchange_xml("".join(el(f"e{i}", "Node", f"n{i}") for i in range(count)))


def test_the_live_tree_does_not_grow_with_the_number_of_elements_already_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peaks = {}
    real = reader.iterparse
    for count in (10_000, 40_000):
        watching = Spy(real)
        monkeypatch.setattr(reader, "iterparse", watching)
        assert len(parse_model(many(count)).elements) == count
        peaks[count] = watching.live_nodes

    assert peaks[10_000] > 0
    assert peaks[40_000] / peaks[10_000] < 2.5  # linear growth would be 4


def test_the_model_keeps_no_reference_to_the_xml_nodes() -> None:
    model = parse_model(many(3))

    for item in model.elements:
        for value in (item.identifier, item.name, item.description, item.path):
            assert type(value) is str  # a plain copy, not a view of a node


def test_reading_stops_at_the_bound_not_at_the_end_of_the_file(
    spy: Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader, "MAX_EXCHANGE_ITEMS", 100)

    with pytest.raises(InvalidExchangeFileError, match="at most 100"):
        parse_model(many(1_000))

    # the frame (4 events) + 101 elements of 4 events each: nothing after the 101st
    assert spy.events <= 4 + 101 * 4 + 1


def test_a_very_deep_document_is_refused_without_recursion() -> None:
    depth = 5_000
    xml = exchange_xml(
        extra="<organizations>" + "<item>" * depth + "</item>" * depth + "</organizations>"
    )

    with pytest.raises(InvalidExchangeFileError, match="nested"):
        parse_model(xml)


class TestAmplification:
    """Small inputs that would become huge trees: each is cut by a bound, none by a big file."""

    def test_the_descendants_of_one_item_are_counted_while_they_stream(
        self, monkeypatch: pytest.MonkeyPatch, spy: Spy
    ) -> None:
        monkeypatch.setattr(reader, "MAX_NODES_PER_ITEM", 10)
        xml = exchange_xml(
            '<element identifier="a" xsi:type="Node">' + "<x/>" * 5_000 + "</element>"
        )

        with pytest.raises(InvalidExchangeFileError, match="too many"):
            parse_model(xml)

        assert spy.events < 100  # stopped at the 11th, not after the 5 000

    def test_an_item_within_the_bound_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(reader, "MAX_NODES_PER_ITEM", 10)
        xml = exchange_xml('<element identifier="a" xsi:type="Node">' + "<x/>" * 10 + "</element>")

        assert len(parse_model(xml).elements) == 1

    def test_attributes_per_node_are_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(reader, "MAX_ATTRIBUTES_PER_NODE", 3)
        crowded = " ".join(f'a{i}="v"' for i in range(4))
        xml = exchange_xml(f'<element identifier="a" xsi:type="Node" {crowded}/>')

        with pytest.raises(InvalidExchangeFileError, match="attributes"):
            parse_model(xml)

    def test_a_token_that_never_ends_is_refused_before_it_is_built(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(reader, "MAX_SILENT_BYTES", 20_000)
        crowded = " ".join(f'a{i}="v"' for i in range(20_000))  # one tag of 200 KB
        xml = exchange_xml(f'<element identifier="a" xsi:type="Node" {crowded}/>')

        with pytest.raises(InvalidExchangeFileError, match="single token"):
            parse_model(xml)

    def test_a_file_of_ordinary_tokens_is_not_taken_for_a_silent_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(reader, "MAX_SILENT_BYTES", 20_000)

        assert len(parse_model(many(2_000)).elements) == 2_000  # 200 KB, thousands of events

    def test_nodes_piling_up_outside_any_item_are_bounded_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(reader, "MAX_LIVE_NODES", 50)
        xml = exchange_xml(extra="<views><view>" + "<x/>" * 500 + "</view></views>")

        with pytest.raises(InvalidExchangeFileError, match="too many"):
            parse_model(xml)

    def test_the_memory_of_a_refused_fan_out_does_not_depend_on_its_size(self) -> None:
        peaks = {}
        for children in (10_000, 40_000):
            xml = exchange_xml(
                '<element identifier="a" xsi:type="Node">' + "<x/>" * children + "</element>"
            )
            tracemalloc.start()
            with pytest.raises(InvalidExchangeFileError):
                parse_model(xml)
            peaks[children] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            del xml

        assert peaks[40_000] / peaks[10_000] < 2.5  # linear growth would be 4 (and the real
        # peaks are dominated by the input string itself, which the caller already holds)
