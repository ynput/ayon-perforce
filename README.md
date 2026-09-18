# AYON Perforce Addon

This addon integrates Perforce with AYON for project-level version control workflows, especially around Unreal projects and Deadline rendering.

It focuses on two major patterns:

- tracking selected published content in Perforce as a source-controlled representation
- syncing artists and render jobs to a specific changelist before opening or rendering an Unreal project

## What it provides

The addon adds Perforce-aware settings and runtime utilities for:

- connecting to a Perforce server and workspace configured in AYON settings
- resolving the local user login and workspace context
- creating or switching workspaces and streams
- validating the local workspace before publishing or launching
- collecting Perforce changelist metadata during publishing
- syncing Unreal projects to a chosen changelist before launch
- passing changelist information into Deadline submissions and farm hooks

## Current implementation

The current client code uses the Perforce CLI (`p4`) directly through subprocess calls rather than a `p4python` dependency. This keeps the addon aligned with the standard Perforce installation already present on artist machines and render workers.

## Settings

Perforce settings are defined in the server-side addon configuration and include:

- `enabled`: enable or disable the Perforce addon per project
- `host_name`: Perforce server host name
- `port`: Perforce server port
- `workspace`: workspace name and depot templates
- `stream`: optional stream-based workspace configuration
- `publish`: profiles that decide when published products should be tracked in Perforce

The default workspace template is generated from AYON project context, and the addon resolves a locally logged-in Perforce user for the current workstation.

## Prerequisites

Before using the addon, make sure:

- the `p4` binary is installed and available on `PATH`
- the Perforce server is reachable from the workstation or render node
- the user is authenticated to the server
- the required workspace or stream exists in Perforce
- the AYON project has the matching template and settings configured

## Core workflows

### 1. Publish-time Perforce tracking

The addon's publish plugins can collect Perforce control metadata for selected products.

This is controlled through the project settings under `publish -> CollectPerforceControl` and can be profiled by:

- host name
- product base types
- task types
- task names

If enabled, the addon can collect and attach information describing a published representation that should be represented as a Perforce changelist.

### 2. Unreal launch sync

Before launching Unreal, the addon can offer an artist a way to sync the project to a previously published changelist instead of leaving the project at an arbitrary local state.

This is handled by launch hooks and is intended for cases where the artist needs to open the project at a known, checked-in state before working on it.

### 3. Deadline / farm integration

The addon also supports the Deadline workflow by collecting changelist metadata and passing it through the launch environment.

Relevant metadata is exposed in environment variables such as `AYON_P4_CHANGELIST`, and the farm pre-launch hook reads that data to ensure the job syncs to the correct changelist before rendering.

## Changelist viewer

A small changelist viewer exists in the client package to show known Perforce changelist references and allow a user to sync to a selected changelist for a project.

This is intended as a convenience for validation, re-rendering, and recovery workflows after a published state has been stored in AYON.

## Notes

This addon is intentionally focused on Perforce and Unreal/Deadline-centric workflows. It is not a general-purpose replacement for the full Perforce client experience; it is designed to integrate Perforce with AYON's project pipeline and render workflows.


