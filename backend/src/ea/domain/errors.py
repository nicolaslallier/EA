"""Domain errors.

They carry no HTTP status and no framework type: `api/` maps them onto the
error envelope, `services/` raises them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ea.domain.package_versions import FrozenRefusal


class DomainError(Exception):
    """Base class for every rule this domain enforces."""


class IllegalRelationshipError(DomainError):
    """A relationship the ArchiMate metamodel does not allow between two types."""


class CyclicContainmentError(DomainError):
    """A composition or aggregation that would close a containment loop."""


class ElementNotFoundError(DomainError):
    """An element referenced by id is absent from the repository."""


class DuplicateElementError(DomainError):
    """An element name is already taken inside its type."""


class DuplicateDocumentError(DomainError):
    """An element already carries a document under that file name."""


class DocumentNotFoundError(DomainError):
    """A document referenced by id is absent from the store."""


class DiagramNotFoundError(DomainError):
    """A diagram referenced by id is absent from the store."""


class DuplicateDiagramError(DomainError):
    """A diagram name is already taken."""


class DiagramRevisionConflictError(DomainError):
    """A layout was saved against a revision someone else has since moved on from."""


class ProfileNotFoundError(DomainError):
    """A profile referenced by id is absent from the store."""


class StereotypeNotFoundError(DomainError):
    """A stereotype referenced by id is absent from the store."""


class DuplicateProfileError(DomainError):
    """A profile name, or a stereotype name within its profile, is already taken."""


class StereotypeInUseError(DomainError):
    """A stereotype is applied to elements and cannot be removed or re-typed."""


class GeneratedViewTooLargeError(DomainError):
    """A view's source yields more elements than a diagram can hold: narrow the filter."""


class GeneratedViewsUnavailableError(DomainError):
    """Generated views were asked for on a deployment without the queries and packages they read."""


class DiagramLiveError(DomainError):
    """A live view has no boxes of its own: it is recomputed at each opening, so not editable."""


class DiagramNotLiveError(DomainError):
    """The diagram is not a live view: there is nothing to freeze."""


class DiagramNotGeneratedError(DomainError):
    """The diagram was drawn by hand: it has no source to freeze, refresh or thaw."""


class DiagramHasAnnotationsError(DomainError):
    """Thawing a view would delete the notes, groups or routes drawn on it."""


class SourceInUseError(DomainError):
    """A query or a package is read by a generated view and cannot be deleted from under it."""


class TemplateNotFoundError(DomainError):
    """A diagram template referenced by id is absent from the store."""


class TemplateLimitError(DomainError):
    """The library holds as many templates as it may: delete one before saving another."""


class TemplateBusyError(DomainError):
    """As many diagrams are being built from templates as the process allows: retry shortly."""


class DuplicateTemplateError(DomainError):
    """A diagram template name is already taken."""


class UnknownLayoutElementError(DomainError):
    """A diagram layout places an element the graph does not hold.

    Its own error rather than `ElementNotFoundError`: that one is a 404 for the
    resource in the URL, and here the diagram exists — it is the body that is
    wrong, which is a 422.
    """


class SearchUnavailableError(DomainError):
    """Semantic search was asked for on a deployment that has no index.

    A deliberate configuration and not a bug — `EA_EMBEDDINGS_ENABLED` is off,
    so nothing was ever embedded — which is why it is a domain failure with a
    message rather than a crash: the caller, agent or human, is told what is
    missing instead of being handed "error executing tool".
    """


class NotAddressableError(DomainError):
    """An IP address was written onto something that cannot answer on one.

    A business process has no interface; an address on it is a modelling
    mistake no later query can undo — see `domain/ipam.py`.
    """


class NotASubnetError(DomainError):
    """A prefix was asked of an element that declares none, or written onto
    something that is not a `communication_network`."""


class DuplicateNetworkError(DomainError):
    """A prefix is already declared in that routing scope."""


class AddressAlreadyAssignedError(DomainError):
    """Another element already answers on that address in that scope."""


class AddressNotAssignedError(DomainError):
    """An address was released, or looked up, and nothing holds it."""


class AddressOutsideAnyNetworkError(DomainError):
    """No declared subnet of that scope holds the address.

    Refused rather than stored: an inventory that accepts addresses belonging
    to no subnet is one nobody can reconcile, and the usual cause is a typo or
    a subnet that was never declared.
    """


class NetworkExhaustedError(DomainError):
    """A subnet was asked for an address and has none left to give."""


class NotAuthenticatedError(DomainError):
    """No caller, or a token that does not prove one. Mapped to 401 / a ToolError."""


class NotAuthorisedError(DomainError):
    """A known caller asking for something its roles do not allow. Mapped to 403."""


class InvalidFileKeyError(DomainError):
    """A file path S3 would accept but that could mean another file: `..`, `//`, a NUL.

    Refused rather than normalised — a path quietly rewritten is a file stored
    somewhere its author did not look for it. See docs/adr/0036.
    """


class InvalidFileMetadataError(DomainError):
    """A title, description or tag outside the bounds of docs/adr/0039.

    A refusal and not a truncation: a description silently cut in half is worse
    than one the person is asked to shorten.
    """


class FileTooLargeError(DomainError):
    """A file beyond what the adapter may hold in memory, or read back as text."""


class FileNotTextError(DomainError):
    """A file asked for as text that is not UTF-8 — a PDF, an image."""


class StoredFileNotFoundError(DomainError):
    """No file at that path in the bucket."""


class StoredFileExistsError(DomainError):
    """A file is already stored at that path, and overwriting it was not asked for."""


class FileStorageUnavailableError(DomainError):
    """File storage was asked for on a deployment that has none (`EA_S3_ENABLED` off).

    A configuration, not a bug — like `SearchUnavailableError` — so the caller is
    told what is missing instead of receiving a 500.
    """


class PackageNotFoundError(DomainError):
    """A package referenced by id is absent from the store."""


class DuplicatePackageError(DomainError):
    """Two packages with the same parent would carry the same name (case ignored)."""


class CyclicPackageError(DomainError):
    """A package that would end up inside itself."""


class PackageNotEmptyError(DomainError):
    """A package still holds sub-packages, elements or diagrams.

    Refused rather than cascaded: deleting elements to tidy a folder would
    destroy facts of the graph on a click.
    """


class PackageTooDeepError(DomainError):
    """A tree deeper than `MAX_PACKAGE_DEPTH` levels."""


class TooManyPackagesError(DomainError):
    """The tree is served in one piece, so it is capped at `MAX_PACKAGES`."""


class UnknownPackageError(DomainError):
    """A stored query names a package that no longer exists.

    Its own error rather than `PackageNotFoundError`: that one is a 404 for the
    resource in the URL, and here the query exists — it is its content that
    went stale, which is a 422.
    """


class InvalidQueryError(DomainError):
    """A stored query that cannot be read back: unknown version, a value the
    enumerations no longer know, or a size beyond what is kept.

    A query is data: it was valid when it was written and the code moved since.
    It is refused with a message, never trusted and never a 500.
    """


class SavedQueryNotFoundError(DomainError):
    """A saved query referenced by id is absent from the store."""


class DuplicateSavedQueryError(DomainError):
    """A saved query name is already taken."""


class InvalidCursorError(DomainError):
    """A pagination cursor this server did not issue — forged, truncated, or from elsewhere.

    A refusal and not a crash: the cursor travels in a URL, and anyone can edit one.
    """


class BaselineNotFoundError(DomainError):
    """A baseline referenced by id is absent from the store."""


class DuplicateBaselineError(DomainError):
    """A baseline name is already taken."""


class BaselineTooLargeError(DomainError):
    """The model is larger than a baseline accepts to copy (elements, or bytes of text).

    Refused before anything is written: a copy that size would be a second
    database, and the limit is declared once in `domain/baselines.py`.
    """


class BaselineScopedError(DomainError):
    """A baseline of one package's subtree cannot be diffed or restored yet.

    Both read a baseline as the whole model: restoring it would plan the deletion of
    everything outside its scope.
    """


class RestoreBlockedError(DomainError):
    """A restoration would change an element that is locked — unlock it first."""


class WriteConflictError(DomainError):
    """The database aborted this write because another one got in its way: replay it."""


class RestoreConflictError(DomainError):
    """A restoration the catalogue refused, or that no longer matches the plan read.

    Nothing was written and no event was recorded. Two causes, told apart by the
    message: a model that moved since the dry run or a concurrent write (read the
    plan again and retry), and a uniqueness clash with something the baseline does
    not cover (retrying the same plan fails the same way until the clash is resolved).
    """


class ElementLockedError(DomainError):
    """An element in review's last stage — approved, or locked — refuses this change."""


class InvalidTransitionError(DomainError):
    """A review transition the element's current state does not offer."""


class ApprovalStaleError(DomainError):
    """An approval names a revision of the element that is no longer the current one.

    An approver certifies what they read: if the element changed since, the
    approval is refused and they read it again.
    """


class InvalidExchangeFileError(DomainError):
    """An exchange file that cannot be read at all: malformed XML, a DTD, an unknown root.

    Its message never repeats the file's content beyond a line number — the file
    is untrusted, and an error message is the first place it would leak into.
    """


class ExchangeFileTooLargeError(DomainError):
    """An exchange file beyond `MAX_EXCHANGE_BYTES`; a 413, unlike `FileTooLargeError`.

    Not a subclass of `FileTooLargeError`: `api/errors.py` looks the status up
    by exact type, and that one is a 422 for the files of the bucket.
    """


class ExchangeBodyTimeoutError(DomainError):
    """The client stopped sending, or sent too slowly, the body of an import (a 408)."""


class ExportInProgressError(DomainError):
    """As many exports are being prepared or sent as the process allows: try again shortly."""


class ExportTooLargeError(DomainError):
    """The file an export would produce is past its ceiling: a narrower scope would fit."""


class ImportInProgressError(DomainError):
    """Another import holds the advisory lock; only one runs at a time."""


class TooManyRefusedRowsError(DomainError):
    """An import with more refused rows than the batches can afford to replay: rejected whole.

    Nothing was written. A file where that many rows are refused is nearly always
    the wrong file, and each refusal costs a replay of the rows before it in its
    batch (docs/adr/0052).
    """


class UnknownScopeError(DomainError):
    """An export scope that does not name what it needs (a package without an id, say)."""


class RowRefusedError(DomainError):
    """One element, relationship or view of an exchange file refused by a rule.

    Never leaves the service: it becomes a line of the import report, so it has
    no HTTP status. `code` is the stable machine-readable name of the refusal.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MatrixAxisTooLargeError(DomainError):
    """An axis of the relationship matrix names more elements than the grid shows."""


class ScopeTooLargeError(DomainError):
    """A calculation was asked of more elements than one run takes."""


class AnalysisBusyError(DomainError):
    """As many analyses are running as the process allows: try again shortly."""


class InvalidAnalysisError(DomainError):
    """A question the analysis cannot answer: a dimension that does not exist, a sum of nothing."""


class AnalysisTimeoutError(DomainError):
    """The database cancelled an analysis for taking too long: narrow the question or the scope."""


class InvalidLifespanError(DomainError):
    """A validity date that is not a day written YYYY-MM-DD, or an end before its start.

    The model keeps `valid_from` / `valid_until` as ISO strings of fixed width so
    that sorting the strings is sorting the days (docs/adr/0059); a partial date
    such as `2027-06` would break exactly that, so it is refused at the door. The
    message names the key and never the value it was given.
    """


class InvalidJunctionError(DomainError):
    """A junction whose `junction_type` is neither `and` nor `or` (docs/adr/0065)."""


class InvalidRequirementError(DomainError):
    """A requirement status, priority or test result outside its vocabulary (docs/adr/0067)."""


class InvalidTimelineError(DomainError):
    """A question about time the model cannot answer: a plateau against itself, an element
    that is not a plateau, a roadmap window that ends before it starts or spans too many years."""


class DuplicateGapError(DomainError):
    """A gap already stands between those two plateaus."""


class ExternalReferenceNotFoundError(DomainError):
    """No external reference carries this id."""


class DuplicateExternalReferenceError(DomainError):
    """This element already cites this external object (docs/adr/0073)."""


class InvalidExternalReferenceError(DomainError):
    """The text is not an identifier or an address the server accepts for that kind."""


class UnknownExternalTargetError(DomainError):
    """The caller named a target the configuration does not declare (or it is not active)."""


class ExternalRefusedError(DomainError):
    """The provider answered, and the object is not there (or not ours to read)."""


class ExternalUnavailableError(DomainError):
    """The provider could not be asked, or answered nothing usable.

    Its message never names the host, the token or the provider's own words:
    those go to the log, where an unauthenticated caller cannot read them.
    """


class ExternalBusyError(DomainError):
    """As many calls to third-party systems are in flight as this process allows."""


class ExternalWriteFailedError(DomainError):
    """The provider answered a write with a refusal or an error (a 502).

    Never carries the provider's body: it is third-party text.
    """


class ExternalWriteTimeoutError(DomainError):
    """The provider did not answer a write in time (a 504) — the object may exist anyway.

    A write that is not idempotent is never retried by the server: the caller checks the
    provider before trying again.
    """


class ReportTooLargeError(DomainError):
    """A report would pass a ceiling: elements, diagrams, document bytes or time.

    The message names the ceiling and how to narrow the scope. A silent truncation would hand
    over a report that looks complete and is not (docs/adr/0062).
    """


class ReportFormatUnavailableError(DomainError):
    """The format needs a native library this deployment does not carry."""


class ReportBusyError(DomainError):
    """As many reports are being built as the process allows (globally, or for this caller)."""


class UnknownReportTemplateError(DomainError):
    """No built-in template has this id."""


class UnsupportedReportSubjectError(DomainError):
    """This template does not take this kind of subject."""


class PackageFrozenError(DomainError):
    """A write reached a package that is published, or lies under one: reopen it first.

    Raised from the database's own refusal (SQLSTATE EA001), translated once in
    `ea.db.unit_of_work`. It names ids, a version, a table and an operation, never
    a name or a text.
    """

    def __init__(self, message: str, refusal: FrozenRefusal | None = None) -> None:
        super().__init__(message)
        self.refusal = refusal


class PackageAlreadyFrozenError(DomainError):
    """A publication aimed at a package that is already published, or under one that is."""


class LiveViewInSubtreeError(DomainError):
    """A subtree that holds a live generated view cannot be published: freeze the view first.

    A live view stores no box, it is recomputed at every opening, so a version could not
    say what it shows.
    """


class PackageHasVersionsError(DomainError):
    """A package that was ever published cannot be deleted: its versions point at it."""


class BaselineInUseError(DomainError):
    """This baseline is the copy of a published version: it can never be deleted."""


class PackageNotFrozenError(DomainError):
    """A reopening aimed at a package that has no version in force."""


class AdrRequiredError(DomainError):
    """A MAJOR publication that names no ADR."""


class InvalidPublicationError(DomainError):
    """Notes, ADR, reason or version that do not read. The message never echoes the value."""


class ExternalLinkFailedError(DomainError):
    """The ticket was opened at the provider but could not be linked here (a 502).

    The message names the ticket's identifier — never its content — and says to link it by hand:
    opening it again would create a second one.
    """


class TooManyExternalReferencesError(DomainError):
    """An element cites as many objects as it may (a 422): remove one before adding another."""
