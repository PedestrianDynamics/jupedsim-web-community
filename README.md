[![CI](https://github.com/PedestrianDynamics/Web-Based-Jupedsim/actions/workflows/ci.yml/badge.svg)](https://github.com/PedestrianDynamics/Web-Based-Jupedsim/actions/workflows/ci.yml)

# Web-Based JuPedSim - Issue Tracker

This repository is to collect feedback and issues when working with [app.jupedsim.org](https://app.jupedsim.org).

## About

[JuPedSim](https://www.jupedsim.org/) is a Python package with a C++ core for simulating pedestrian dynamics. The web-based interface provides an accessible frontend that communicates with the JuPedSim backend, allowing users to:

- Create, upload, and download simulation scenarios
- Run pedestrian dynamics simulations
- Visualize results directly in the browser

> [!IMPORTANT]
> **This is still a work in progress!** We count on user feedback to enhance the app and add more features in the future.

## How to Use This Repository

- **Found a bug?** [Open an issue](../../issues/new)
- **Have a feature request?** [Open an issue](../../issues/new)
- **Need help?** Check existing issues or open a new one
- **Want to discuss?** Use the [Discussions](../../discussions) tab

> **Note:** This repository does not contain the application code (which is private). It's solely for tracking issues, feedback, and documentation.

## Supported Geometry Formats

The app supports **DXF files** with a specific layer naming convention:

| Layer Name | Type | Description |
|------------|------|-------------|
| **jps-walkablearea** | **Mandatory** | A polyline containing all other simulation elements. No overlaps allowed. |
| **jps-obstacles** | Optional | Static obstacles within the simulation area |
| **jps-distributions** | **Mandatory** | Initial positions/distributions of pedestrians |
| **jps-exits** | **Mandatory** | Exit points for pedestrians |
| **jps-waypoints** | Optional | Intermediate target points for pedestrian navigation |
| **jps-journeys** | Optional | Predefined paths or routes |

See [template DXF-file](templates/gallery.dxf).

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

## Links

- **Web App:** [app.jupedsim.org](https://app.jupedsim.org)
- **JuPedSim Documentation:** [jupedsim.org](https://www.jupedsim.org/)
- **JuPedSim on GitHub:** [github.com/PedestrianDynamics/jupedsim](https://github.com/PedestrianDynamics/jupedsim)

## License

This documentation is provided under the MIT License.
