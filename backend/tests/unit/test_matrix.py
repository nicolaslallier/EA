"""The relationship matrix as a pure function of two axes and the links."""

from __future__ import annotations

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.matrix import MAX_MATRIX_AXIS, MatrixAxis, allowed_between, build_matrix
from tests.support_analysis import a_link, an_element


def axis(*elements):  # type: ignore[no-untyped-def]
    return MatrixAxis.of(elements)


def test_a_matrix_of_nothing_is_empty() -> None:
    matrix = build_matrix(axis(), axis(), [])

    assert (matrix.cells, matrix.blocked) == ((), ())
    assert not matrix.rows.truncated


def test_a_link_lands_in_the_cell_of_its_source_row_and_target_column() -> None:
    api = an_element(E.APPLICATION_SERVICE, "Invoice API")
    process = an_element(E.BUSINESS_PROCESS, "Order to cash")
    link = a_link(R.SERVING, api, process)

    matrix = build_matrix(axis(api), axis(process), [link])

    assert [(c.source_id, c.target_id, c.links) for c in matrix.cells] == [
        (api.id, process.id, (link,))
    ]
    assert R.SERVING in matrix.cells[0].allowed


def test_two_links_between_one_pair_share_a_cell() -> None:
    api = an_element(E.APPLICATION_SERVICE, "Invoice API")
    process = an_element(E.BUSINESS_PROCESS, "Order to cash")
    serving, association = a_link(R.SERVING, api, process), a_link(R.ASSOCIATION, api, process)

    matrix = build_matrix(axis(api), axis(process), [serving, association])

    assert len(matrix.cells) == 1
    assert {link.id for link in matrix.cells[0].links} == {serving.id, association.id}


def test_the_two_directions_are_two_cells_and_never_mirrored() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")
    forward, backward = a_link(R.FLOW, a, b), a_link(R.FLOW, b, a)

    matrix = build_matrix(axis(a, b), axis(a, b), [forward])
    assert [(c.source_id, c.target_id) for c in matrix.cells] == [(a.id, b.id)]

    both = build_matrix(axis(a, b), axis(a, b), [forward, backward])
    assert {(c.source_id, c.target_id) for c in both.cells} == {(a.id, b.id), (b.id, a.id)}


def test_a_link_leaving_the_axes_is_not_drawn() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")
    outsider = an_element(E.APPLICATION_COMPONENT, "Outsider")

    matrix = build_matrix(axis(a), axis(b), [a_link(R.FLOW, a, outsider)])

    assert matrix.cells == ()


def test_the_diagonal_is_not_special_the_rules_decide() -> None:
    component = an_element(E.APPLICATION_COMPONENT, "A")

    allowed = allowed_between(component, component)

    assert R.COMPOSITION not in allowed
    assert R.SPECIALIZATION not in allowed
    assert R.ASSOCIATION in allowed


def test_an_element_on_both_axes_gets_a_row_and_a_column() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")

    matrix = build_matrix(axis(a, b), axis(a, b), [a_link(R.FLOW, a, b)])

    assert len(matrix.cells) == 1
    assert matrix.blocked == ()


def test_a_pair_with_nothing_permitted_and_no_link_is_blocked() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")

    matrix = build_matrix(axis(a), axis(b), [], allowed=lambda source, target: ())

    assert matrix.cells == ()
    assert matrix.blocked == ((a.id, b.id),)


def test_an_axis_over_the_ceiling_is_cut_and_says_how_many_there_were() -> None:
    many = [an_element(E.NODE, f"n{index:03}") for index in range(MAX_MATRIX_AXIS + 3)]

    cut = MatrixAxis.of(many[: MAX_MATRIX_AXIS + 1], total=MAX_MATRIX_AXIS + 3)

    assert len(cut.elements) == MAX_MATRIX_AXIS
    assert cut.total == MAX_MATRIX_AXIS + 3
    assert cut.truncated


def test_an_axis_total_is_never_below_what_it_keeps() -> None:
    nodes = [an_element(E.NODE, "a"), an_element(E.NODE, "b")]

    assert MatrixAxis.of(nodes, total=0).total == 2
