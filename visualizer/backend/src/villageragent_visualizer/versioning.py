from __future__ import annotations

from dataclasses import dataclass
import re

from villageragent_visualizer.dto import (
    ArtifactErrorCode,
    ArtifactLoadError,
    ArtifactWarning,
    ArtifactWarningCode,
)


_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True, order=True, slots=True)
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SemVer | None:
        match = _SEMVER_PATTERN.fullmatch(value)
        if match is None:
            return None
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
        )


@dataclass(frozen=True, slots=True)
class SchemaValidation:
    version: str | None
    warnings: tuple[ArtifactWarning, ...] = ()
    error: ArtifactLoadError | None = None


def validate_schema_version(
    schema_version: object,
    *,
    supported_version: str | None,
) -> SchemaValidation:
    if schema_version is None:
        return SchemaValidation(
            version=None,
            warnings=(ArtifactWarning(
                code=ArtifactWarningCode.PRODUCER_VERSIONED,
                message="Artifact has no schema_version and is versioned by its producer.",
            ),),
        )

    if not isinstance(schema_version, str):
        if supported_version is None:
            return SchemaValidation(version=str(schema_version))
        return SchemaValidation(
            version=str(schema_version),
            error=ArtifactLoadError(
                code=ArtifactErrorCode.INVALID_SCHEMA_VERSION,
                message="Artifact schema_version must be a semantic version string.",
            ),
        )

    if supported_version is None:
        return SchemaValidation(version=schema_version)

    actual = SemVer.parse(schema_version)
    supported = SemVer.parse(supported_version)
    if actual is None or supported is None:
        return SchemaValidation(
            version=schema_version,
            error=ArtifactLoadError(
                code=ArtifactErrorCode.INVALID_SCHEMA_VERSION,
                message=f"Invalid semantic schema version: {schema_version!r}.",
            ),
        )

    if actual.major != supported.major:
        return SchemaValidation(
            version=schema_version,
            error=ArtifactLoadError(
                code=ArtifactErrorCode.UNSUPPORTED_SCHEMA_MAJOR,
                message=(
                    f"Artifact schema major {actual.major} is incompatible with supported major "
                    f"{supported.major}."
                ),
            ),
        )

    warnings: tuple[ArtifactWarning, ...] = ()
    if actual > supported:
        warnings = (ArtifactWarning(
            code=ArtifactWarningCode.FUTURE_SCHEMA_VERSION,
            message=(
                f"Artifact schema {schema_version} is newer than supported schema "
                f"{supported_version}; unknown fields are preserved."
            ),
        ),)
    return SchemaValidation(version=schema_version, warnings=warnings)
