#!/usr/bin/env -S deno run --ext ts
// Copyright Sebastian Wiesner <sebastian@swsnr.de>
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
// License for the specific language governing permissions and limitations under
// the License.

// Regenerate the manifest containing all maven dependencies, the hacky way.
//
// Maven doesn't seem to have any way to just dump all resolved dependencies in
// a structured data format, so we manually resolve things and then grep the
// log file for all downloaded artifacts.

import * as path from "jsr:@std/path@1";
import * as yaml from "jsr:@std/yaml@1";
import * as fs from "jsr:@std/fs@1";

type SourceType = "archive" | "file" | "patch";

interface FileSource {
    readonly type: SourceType;
    readonly path: string;
    readonly url: undefined;
}

interface UrlSource {
    readonly type: SourceType;
    readonly url: string;
    readonly sha512: string;
    readonly dest?: string;
    readonly "dest-filename"?: string;
}

interface GitSource {
    readonly type: "git";
    readonly url: string;
    readonly tag: string;
    readonly commit: string;
}

type Source = FileSource | UrlSource | GitSource;

/**
 * Run a command and return its output.
 *
 * If the command returns a non-zero exit code, throw an error.
 *
 * @param command The command to run
 * @param options Additional options for the command
 * @returns The output of the command
 */
const checkedRun = async (
    command: readonly string[],
    options?: Deno.CommandOptions,
): Promise<Deno.CommandOutput> => {
    const output = await new Deno.Command(command[0], {
        args: command.slice(1),
        ...options,
    }).output();
    if (output.code !== 0) {
        // If the command failed, dump its output for easier debugging.
        const decoder = new TextDecoder();
        console.info(decoder.decode(output.stdout));
        console.error(decoder.decode(output.stderr));
        throw new Error(`Failed to run command ${command.join(" ")}`);
    }
    return output;
};

/**
 * Determine whether the given flatpak module source refers to Git.
 *
 * @param source The source
 * @returns Whether `source` is a git source
 */
const isGitSource = (source: string | Source): source is GitSource =>
    typeof source !== "string" && source.type === "git";

interface Module {
    readonly name: string;
    readonly sources: (string | Source)[];
}

interface Manifest {
    readonly modules: readonly Module[];
}

/**
 * Get the main module source for Mediathekview from the manifest.
 *
 * Read the manifest and find the one module source pointing to the source
 * tarball of Mediathekview.
 *
 * @param manifestDirectory The directory containing the manifest
 * @returns The main source
 */
const getMainSource = async (manifestDirectory: string): Promise<GitSource> => {
    const manifestPath = path.join(
        manifestDirectory,
        "de.mediathekview.MediathekView.yaml",
    );
    const manifest = yaml
        .parse(await Deno.readTextFile(manifestPath)) as Manifest;
    const module = manifest.modules.find((m) => m.name === "mediathekview");
    if (!module) {
        throw new Error("Failed to find mediathekview module");
    }
    const source = module.sources
        .filter(isGitSource)
        .find((source) => source.url.includes("mediathekview"));
    if (!source) {
        throw new Error("No archive source found for MediathekView");
    }
    return source;
};

/**
 * Calculate the SHA512 digest of a file.
 *
 * @param file The file to compute the checksum of
 * @returns The sha512 digest as hexadecimal string
 */
const sha512sum = async (file: string): Promise<string> => {
    const digest = await crypto.subtle
        .digest("SHA-512", await Deno.readFile(file));
    return Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
};

interface ArtifactURL {
    /**
     * The full URL of an artifact.
     */
    readonly url: string;
    /**
     * The base url of the repository the artifact was downloaded from.
     */
    readonly repoBaseUrl: string;
    /**
     * The URL path relative to `repoBaseUrl`.
     */
    readonly relpath: string;
}

/**
 * Known base URLs of Maven repositories.
 */
const REPO_BASES = [
    "https://repo.maven.apache.org/maven2/",
    "https://oss.sonatype.org/content/repositories/snapshots/",
    "https://maven.ej-technologies.com/repository/",
];

/**
 * Parse a URL point to an artifact in a maven repository.
 *
 * @param url The URL of an artifact.
 * @returns The parsed URL.
 */
const parseUrl = (url: string): ArtifactURL => {
    for (const base of REPO_BASES) {
        if (url.startsWith(base)) {
            return {
                url,
                repoBaseUrl: base,
                relpath: url.slice(base.length),
            };
        }
    }
    throw new Error(`Failed to determine repository base of ${url}`);
};

/**
 * Extracts all downloaded artifacts from a build log of a Maven build.
 *
 * @param mavenOutput The full output of a Maven build
 * @returns A list of all artifacts that were downloaded during thebuild.
 */
const extractDownloadedArtifacts = (mavenOutput: string): ArtifactURL[] => {
    const urls = [];
    for (const line of mavenOutput.split("\n")) {
        const match = line.match(/Downloaded from .*: (https?:\/\/[^ ]+)/);
        if (!match) {
            continue;
        }
        urls.push(parseUrl(match[1]));
    }
    return urls;
};

/**
 * Create a flatpak source object from an artifact URL.
 *
 * Calculate the SHA 512 checksum of the artifact, and define a file structure
 * to reconstruct its place in a maven repository.
 *
 * @param repoDirectory The repository directory
 * @param url The artifact URL
 * @returns A corresponding flatpak URL
 */
const artifactUrlToFlatpakSource = async (
    repoDirectory: string,
    url: ArtifactURL,
): Promise<UrlSource> => {
    const dest = path.dirname(path.join(".m2/repository", url.relpath));
    let destname = path.basename(url.relpath);
    const dir = path.dirname(path.join(repoDirectory, url.relpath));
    if (destname === "maven-metadata.xml") {
        const candidates = fs.expandGlob(path.join(dir, "maven-metadata*.xml"));
        for await (const entry of candidates) {
            destname = entry.name;
        }
    }
    const sha512 = await sha512sum(path.join(dir, destname));
    return {
        type: "file",
        url: url.url,
        dest,
        "dest-filename": destname,
        sha512,
    };
};

/**
 * Run a block with a temporary directory.
 *
 * Create a temporary directory, pass it to `block`, and then delete the
 * temporary directory afterwards.
 *
 * @param block The block to run
 * @returns The return value of `block`
 */
const withTempDir = async <T>(
    block: (tempDir: string) => T | Promise<T>,
): Promise<T> => {
    const tempDir = await Deno
        .makeTempDir({ prefix: "mediathekview-dependencies-" });
    try {
        return await block(tempDir);
    } finally {
        try {
            await fs.emptyDir(tempDir);
            await Deno.remove(tempDir);
        } catch (error) {
            console.error("Failed to delete", tempDir, error);
        }
    }
};

const main = () =>
    withTempDir(async (workingDirectory) => {
        const manifestDirectory = import.meta.dirname;
        if (!manifestDirectory) {
            throw new Error("Not running as local module?");
        }

        const source = await getMainSource(manifestDirectory);
        const repoDirectory = path.join(workingDirectory, "repo");
        const sourceDirectory = path.join(workingDirectory, "mediathekview");
        await Promise
            .all([sourceDirectory, repoDirectory].map((d) => Deno.mkdir(d)));
        await checkedRun(["git", "init"], { cwd: sourceDirectory });
        await checkedRun([
            "git",
            "fetch",
            "--depth=1",
            source.url,
            source.commit,
        ], { cwd: sourceDirectory });
        await checkedRun(["git", "reset", "--hard", source.commit], {
            cwd: sourceDirectory,
        });

        // TODO: Do we need to run a full "install"?
        const output = (await checkedRun(
            [
                path.join(sourceDirectory, "mvnw"),
                // Enable batch mode for non-interactive builds, which
                // implicitly disables coloured output and thus makes parsing a
                // lot easier.
                "-B",
                // Make maven use a fresh local repo in our temporary directory.
                // This makes sure that we definitely download all dependencies.
                `-Dmaven.repo.local=${repoDirectory}`,
                "clean",
                "install",
            ],
            {
                cwd: sourceDirectory,
                stdout: "piped",
            },
        )).stdout;

        const urls = extractDownloadedArtifacts(
            new TextDecoder().decode(output),
        );
        const sources = await Promise
            .all(urls.map((u) => artifactUrlToFlatpakSource(repoDirectory, u)));
        const collator = Intl.Collator();
        sources.sort((a, b) => collator.compare(a.url, b.url));
        Deno.writeTextFile(
            path.join(manifestDirectory, "maven-dependencies.json"),
            JSON.stringify(sources, undefined, 2),
        );
    });

if (import.meta.main) {
    main();
}
