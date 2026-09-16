[![IMO Tests](https://github.com/PedestrianDynamics/jupedsim-web-community/actions/workflows/imo.yml/badge.svg)](https://github.com/PedestrianDynamics/jupedsim-web-community/actions/workflows/imo.yml)
[![RiMEA Tests](https://github.com/PedestrianDynamics/jupedsim-web-community/actions/workflows/rimea.yml/badge.svg)](https://github.com/PedestrianDynamics/jupedsim-web-community/actions/workflows/rimea.yml)
[![ISO Tests](https://img.shields.io/badge/ISO%20Tests-planned-lightgrey)](https://github.com/PedestrianDynamics/jupedsim-web-community/issues)
[![Docker Pulls](https://img.shields.io/docker/pulls/jupedsim/jupedsim-web)](https://hub.docker.com/r/jupedsim/jupedsim-web)
[![Docker Image Version](https://img.shields.io/docker/v/jupedsim/jupedsim-web?sort=semver&label=docker%20tag)](https://hub.docker.com/r/jupedsim/jupedsim-web/tags)
[![jupedsim on PyPI](https://img.shields.io/pypi/v/jupedsim?label=jupedsim)](https://pypi.org/project/jupedsim/)
[![jupedsim-scenarios on PyPI](https://img.shields.io/pypi/v/jupedsim-scenarios?label=jupedsim-scenarios)](https://pypi.org/project/jupedsim-scenarios/)

[![Watch on YouTube](https://img.youtube.com/vi/MGj0Nyumdms/0.jpg)](https://www.youtube.com/watch?v=MGj0Nyumdms)


# JuPedSim Web Community
This public repository is the community home for JuPedSim Web. It started as an issue tracker and now also contains public documentation, local tooling, shared scenario runtime modules, and example scenarios for running exported setups outside the hosted app.

## About

[JuPedSim](https://www.jupedsim.org/) is a Python package with a C++ core for simulating pedestrian dynamics. JuPedSim Web provides a browser-based interface on top of it, and this repository complements that app with public-facing community and scripting resources.

With JuPedSim Web you can:

- Create, upload, and download simulation scenarios
- Import geometry from DXF and IFC files
- Run pedestrian dynamics simulations
- Visualize results directly in the browser

With this repository you can:

- Report issues and discuss feature ideas
- Run the public Docker setup locally
- Execute public example scenarios from Python
- Inspect public V&V assets and workflows



> [!IMPORTANT]
> **This is still a work in progress!** We count on user feedback to enhance the app and add more features in the future.

## Quick Start

### Hosted App

- **Web App:** [app.jupedsim.org](https://app.jupedsim.org)
- **JuPedSim Documentation:** [jupedsim.org](https://www.jupedsim.org/)
- **JuPedSim on GitHub:** [github.com/PedestrianDynamics/jupedsim](https://github.com/PedestrianDynamics/jupedsim)

### Run Locally With Docker

You can run the public JuPedSim Web setup locally. See [docker/README.md](docker/README.md) for the full setup and troubleshooting notes.

### Run Public Scenarios Locally

The [`standards/`](standards/) directory contains a standalone Python workflow for running exported scenarios locally, including waiting stages, zones, and example scenarios.

```bash
uv sync --extra dev
cd standards
uv run jupyter notebook
```

Start here:

- [standards/README.md](standards/README.md)
- [standards/rimea/scenario_builders/](standards/rimea/scenario_builders/)

> General scenario-scripting examples (loading, sweeping, plotting) live upstream in the
> [jupedsim-scenarios cookbook](https://github.com/PedestrianDynamics/jupedsim-scenarios/tree/main/examples).
> This repository keeps only the standards-specific notebooks (RiMEA, etc.).

## Repository Layout

- [`docker/`](docker/) contains the public local deployment setup for JuPedSim Web.
- [`docker/llm-bridge/`](docker/llm-bridge/) is an experimental HTTP bridge that lets a human or an LLM agent drive the local viewer.
- [`standards/`](standards/) contains the local Python runner, notebooks, and example scenarios.
- [`tests/vv/`](tests/vv/) contains verification and validation assets and workflows.
- [`geometries/`](geometries/) contains public geometry examples and format references.

## Community Use

- **Found a bug?** [Open an issue](../../issues/new)
- **Have a feature request?** [Open an issue](../../issues/new)
- **Need help?** Check existing issues or open a new one
- **Want to discuss?** Use the [Discussions](../../discussions) tab

> [!NOTE]
> The private JuPedSim Web application implementation is not in this repository. This repository contains the public community-facing parts around it.

## Supported Geometry Formats

The app supports **DXF** and **IFC** geometry inputs.

For **DXF** files, use the required layer naming convention:

| Layer Name | Type | Description |
|------------|------|-------------|
| **jps-walkablearea** | **Mandatory** | A polyline containing all other simulation elements. No overlaps allowed. |
| **jps-obstacles** | Optional | Static obstacles within the simulation area |
| **jps-distributions** | **Mandatory** | Initial positions/distributions of pedestrians |
| **jps-exits** | **Mandatory** | Exit points for pedestrians |
| **jps-waypoints** | Optional | Intermediate target points for pedestrian navigation |
| **jps-journeys** | Optional | Predefined paths or routes |

See:

- [Preparing DXF Files](https://github.com/PedestrianDynamics/jupedsim-web-community/wiki/Preparing-DXF-Files)
- [geometries/dxf/](geometries/dxf/)
- [geometries/ifc/](geometries/ifc/)

### Important Notes:
- The **walkable area** layer must be a closed polyline that encompasses all other elements
- No overlapping geometries are allowed in the walkable area
- Layer names are case-sensitive


## Reporting Issues

When reporting issues, please include:

- **Browser and version** (e.g., Chrome 120, Firefox 121)
- **Operating system** (e.g., Windows 11, macOS 14, Ubuntu 22.04)
- **Steps to reproduce** the issue
- **Expected behavior** vs. **actual behavior**
- **Screenshots or screen recordings** if applicable
- **DXF file** (if the issue is geometry-related)

## Scope

This repository is public and community-facing. It is the right place for issues, discussions, docs, local examples, and public scenario tooling. Private backend, web application, and deployment internals remain outside this repository.

## License

This documentation is provided under the MIT License.
