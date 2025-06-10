#!/usr/bin/env python3
# Copyright Sebastian Wiesner <sebastian@swsnr.de>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

"""
Regenerate the manifest containing all maven dependencies, the hacky way.

Maven doesn't seem to have any way to just dump all resolved dependencies in
a structured data format, so we manually resolve things and then grep the
log file for all downloaded artifacts.
"""

import glob
import hashlib
import json
import os
import re
import shlex
import sys
from collections.abc import Iterator
from pathlib import Path, PosixPath
from dataclasses import dataclass
from subprocess import CalledProcessError, run
from tempfile import TemporaryDirectory
from typing import Any, override
from argparse import ArgumentParser


@dataclass(kw_only=True, frozen=True)
class FlatpakGitSource:
    """A git source in a flatpak manifest."""

    # The git URL
    url: str

    # The commit
    commit: str


@dataclass(kw_only=True, frozen=True)
class FlatpakFileSource:
    url: str
    dest: str
    dest_filename: str
    sha512: str

    def as_json(self) -> dict[str, str]:
        return {
            "type": "file",
            "url": self.url,
            "dest": self.dest,
            "dest-filename": self.dest_filename,
            "sha512": self.sha512,
        }


@dataclass(kw_only=True, frozen=True)
class FlatpakSdk:
    """A flatpak SDK by name and version"""

    name: str
    version: str

    @classmethod
    def parse_from_manifest(cls, manifest: str) -> "FlatpakSdk":
        name = version = None
        for line in manifest.splitlines():
            if line.startswith("sdk:"):
                name = line.split(":", maxsplit=1)[1].strip()
            elif line.startswith("runtime-version"):
                version = line.split(":", maxsplit=1)[1].strip().strip("'\"")

            if name and version:
                return cls(name=name, version=version)

        raise LookupError("Failed to find sdk or runtime-version in manifest")

    @override
    def __str__(self):
        return f"{self.name}//{self.version}"


# Known base URLs of Maven repositories used by MediathekView.
REPO_BASES = [
    "https://repo.maven.apache.org/maven2/",
    "https://oss.sonatype.org/content/repositories/snapshots/",
    "https://maven.ej-technologies.com/repository/",
]


@dataclass(kw_only=True, frozen=True)
class ArtifactUrl:
    """A maven artifact URL."""

    # The full URL of the artifact
    url: str

    # The base url of the repository the artifact was downloaded from.
    repo_base_url: str

    # The URL path relative to `repo_base_url`.
    relpath: str

    @classmethod
    def parse_url(cls, url: str) -> "ArtifactUrl":
        for base in REPO_BASES:
            if url.startswith(base):
                return ArtifactUrl(
                    url=url, repo_base_url=base, relpath=url[len(base) :]
                )
        else:
            raise ValueError(f"{url} is not from a known Maven repository")


def find_mediathekview_source(manifest: Path) -> FlatpakGitSource:
    """Extract the main source from the `manifest`.

    Args:
        manifest (Path): The manifest to get the source from

    Raises:
        LookupError: If the module or its main source aren't found

    Returns:
        FlatpakGitSource: The main source
    """
    lines = manifest.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if line.strip() == "- name: mediathekview":
            break
    else:
        raise LookupError("mediathekview module not found")
    for line in lines:
        if line.strip() == "- type: git":
            break
    else:
        raise LookupError("git source not found")
    url = commit = None
    for line in lines:
        line = line.strip()
        if line.startswith("url:"):
            url = line.split(":", maxsplit=1)[1].strip()
        elif line.startswith("commit:"):
            commit = line.split(":", maxsplit=1)[1].strip()
        if url and commit:
            return FlatpakGitSource(url=url, commit=commit)
    raise LookupError("url or commit not found in main mediathekview source")


DOWNLOADED_RE = re.compile("Downloaded from .*: (https?://[^ ]+)")


def extract_downloaded_artifacts(build_log: str) -> Iterator[ArtifactUrl]:
    """Extract Maven artifact URLs from a Maven build log.

    Args:
        build_log (str): The stdout of a Maven build

    Yields:
        Iterator[ArtifactUrl]: Extracted Maven Artifact URLs.
    """
    for line in build_log.splitlines():
        match = DOWNLOADED_RE.search(line)
        if match:
            yield ArtifactUrl.parse_url(match.group(1))


def create_flatpak_source(repo: Path, url: ArtifactUrl) -> FlatpakFileSource:
    """Convert an artifact URL from a repo to a flatpak file source.

    Args:
        repo (Path): The local repository directory to read artifact files from
        url (ArtifactUrl): The artifact URL to create a source for

    Returns:
        FlatpakFileSource: A flatpak file source for the artifact
    """
    artifact = repo / url.relpath
    dest_filename = artifact.name
    # artifact = PosixPath(".m2") / "repository" / url.relpath
    if dest_filename == "maven-metadata.xml":
        candidates = glob.glob("maven-metadata-*.xml", root_dir=artifact.parent)
        if candidates:
            dest_filename = candidates[0]
    return FlatpakFileSource(
        url=url.url,
        dest=(PosixPath(".m2") / "repository" / url.relpath).parent.as_posix(),
        dest_filename=dest_filename,
        sha512=hashlib.sha512(artifact.read_bytes()).hexdigest(),
    )


def update_dependencies(source: FlatpakGitSource) -> list[FlatpakFileSource]:
    """Update Maven dependencies.

    Clone and build `source`, extract all artifact URLs from the build log, and
    update `target` with a Flatpak manifest containing all artifacts.

    Args:
        source (FlatpakGitSource): The source to get dependencies for
        target (Path): The file to write the dependencies to
    """
    prefix = "de.mediathekview.MediathekView-dependencies-"
    with TemporaryDirectory(prefix=prefix) as working_directory:
        working_directory = Path(working_directory)
        source_directory = working_directory / "mediathekview"

        cmd = [
            "git",
            "-c",
            "advice.detachedHead=false",
            "clone",
            "--depth=1",
            f"--revision={source.commit}",
            source.url,
            str(source_directory),
        ]
        print("Running {}".format(shlex.join(cmd)))
        _ = run(cmd, check=True)

        repo_directory = working_directory / "repo"
        repo_directory.mkdir()
        cmd = [
            str(source_directory / "mvnw"),
            # Enable batch mode for non-interactive builds, which
            # implicitly disables coloured output and thus makes parsing a
            # lot easier.
            "-B",
            # Make maven use a fresh local repo in our temporary directory.
            # This makes sure that we definitely download all dependencies.
            # Otherwise we might miss dependencies not downloaded and thus
            # not appearing in the log.
            f"-Dmaven.repo.local={repo_directory}",
            "clean",
            # Run package as that's the same goal we run in the manifest, to
            # make sure we get everything that this goal requires
            "package",
        ]
        print("Running {}".format(shlex.join(cmd)))
        mvnw = run(
            cmd,
            cwd=source_directory,
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        sources = [
            create_flatpak_source(repo_directory, url)
            for url in extract_downloaded_artifacts(mvnw.stdout)
        ]
        sources.sort(key=lambda u: u.url)
        return sources


class SourceEncoder(json.JSONEncoder):
    @override
    def default(self, o: Any) -> Any:
        if isinstance(o, FlatpakFileSource):
            return o.as_json()
        return super().default(o)


def run_direct(manifest: Path) -> None:
    source = find_mediathekview_source(manifest)
    target = manifest.parent / "maven-dependencies.json"
    sources = update_dependencies(source)
    with target.open("w", encoding="utf-8") as sink:
        json.dump(sources, sink, indent=2, cls=SourceEncoder)


def run_in_flatpak(manifest: Path) -> None:
    base_sdk = FlatpakSdk.parse_from_manifest(manifest.read_text())
    sdks = [
        base_sdk,
        FlatpakSdk(
            name="org.freedesktop.Sdk.Extension.openjdk",
            version=base_sdk.version,
        ),
    ]
    cmd = [
        "flatpak",
        "install",
        "--user",
        "--noninteractive",
        "--assumeyes",
    ]
    cmd.extend(str(s) for s in sdks)
    print("Running {}".format(shlex.join(cmd)))
    _ = run(cmd, check=True)

    manifest_directory = manifest.parent.absolute()
    cmd = [
        "flatpak",
        "run",
        "--share=network",
        "--command=/bin/bash",
        "--filesystem={}".format(manifest_directory.as_posix()),
        "--cwd={}".format(manifest_directory.as_posix()),
        str(base_sdk),
        "-c",
        "source /usr/lib/sdk/openjdk/enable.sh; {}/update-dependencies.py".format(
            shlex.quote(manifest_directory.as_posix())
        ),
    ]
    print("Running {}".format(shlex.join(cmd)))
    _ = run(cmd, check=True)


def main() -> None:
    try:
        if os.geteuid() == 0:
            sys.exit("Do not run this as root!")

        parser = ArgumentParser()
        _ = parser.add_argument(
            "--flatpak",
            action="store_true",
            help="Run inside flatpak",
        )

        manifest = Path(__file__).parent / "de.mediathekview.MediathekView.yaml"

        args = parser.parse_args()
        if args.flatpak:
            run_in_flatpak(manifest)
        else:
            run_direct(manifest)
    except KeyboardInterrupt:
        sys.exit("Interrupted, dependencies not updated!")
    except CalledProcessError as error:
        if error.stderr:
            sys.exit(
                f"Command {error.cmd!r} failed with exit code {1}:\n{error.stderr}"
            )
        else:
            sys.exit(f"Command {error.cmd!r} failed with exit code {1}")


if __name__ == "__main__":
    main()
