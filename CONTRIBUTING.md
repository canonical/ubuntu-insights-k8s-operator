# Contributing to the Ubuntu Insights charm

A big welcome and thank you for considering contributing to the Ubuntu Insights Juju charm! It’s people like you that make it a reality for users in our community.

Reading and following these guidelines will help us make the contribution process easy and effective for everyone involved. It also communicates that you agree to respect the time of the developers managing and developing this project. In return, we will reciprocate that respect by addressing your issue, assessing changes, and helping you finalize your pull requests.

These are mostly guidelines, not rules. Use your best judgment, and feel free to propose changes to this document in a pull request.

## Quick Links

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Issues](#issues)
- [Pull Requests](#pull-requests)
- [Contributing to the code](#contributing-to-the-code)
- [Contributor License Agreement](#contributor-license-agreement)
- [Getting Help](#getting-help)

## Code of Conduct

We take our community seriously and hold ourselves and other contributors to high standards of communication. By participating and contributing to this project, you agree to uphold our [Code of Conduct](https://ubuntu.com/community/code-of-conduct).

## Getting Started

Contributions are made to this project via Issues and Pull Requests (PRs). A few general guidelines that cover both:

- To report security vulnerabilities, please use the advisories page of the repository and not a public bug report.
- Search for existing Issues and PRs on this repository before creating your own.
- We work hard to makes sure issues are handled in a timely manner but, depending on the impact, it could take a while to investigate the root cause. A friendly ping in the comment thread to the submitter or a contributor can help draw attention if your issue is blocking.
- If you've never contributed before, see [this Ubuntu resource post](https://ubuntu.com/community/contribute) for resources and tips on how to get started.

### Issues

Issues should be used to report problems with the software, request a new feature, or to discuss potential changes before a PR is created. When you create a new Issue, a template will be loaded that will guide you through collecting and providing the information we need to investigate.

If you find an issue that addresses the problem you're having, please add your own reproduction information to the existing issue rather than creating a new one. Adding a [reaction](https://github.blog/2016-03-10-add-reactions-to-pull-requests-issues-and-comments/) can also help be indicating to our maintainers that a particular problem is affecting more than just the reporter.

### Pull Requests

PRs to our project are always welcome and can be a quick way to get your fix or improvement slated for the next release. In general, PRs should:

- Only fix/add the functionality in question **OR** address wide-spread whitespace/style issues, not both.
- Add unit or integration tests for fixed or changed functionality.
- Address a single concern in the least number of changed lines as possible.
- Include documentation in the repo.
- Be accompanied by a complete Pull Request template (loaded automatically when a PR is created).

For changes that address core functionality or would require breaking changes (e.g. a major release), it's best to open an Issue to discuss your proposal first. This is not required but can save time creating and reviewing changes.

In general, we follow the ["fork-and-pull" Git workflow](https://github.com/susam/gitpr)

1. Fork the repository to your own GitHub account
1. Clone the project to your machine
1. Create a branch locally with a succinct but descriptive name
1. Commit changes to the branch
1. Following any formatting and testing guidelines specific to this repo
1. Push changes to your fork
1. Open a PR in our repository and follow the PR template so that we can efficiently review the changes.

> PRs will trigger unit and integration tests, linting and formatting validations, static and security checks. All the tests must pass before merging in main branch.

## Contributing to the code

### Required dependencies

To contribute to this charm, you will need a working [development setup](https://documentation.ubuntu.com/juju/latest/howto/manage-your-deployment/index.html).

The code for this charm can be downloaded as follows:

```
git clone https://github.com/canonical/ubuntu-insights-k8s-operator
```

You can create an environment for development with [`uv`](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv sync --group test
```

### Building the rock and charm

Use [Rockcraft](https://documentation.ubuntu.com/rockcraft/en/latest/) to create an OCI image for the Ubuntu Insights services, and then upload the image to the MicroK8s registry which stores OCI archives so they can be downloaded and deployed.

Enable the MicroK8s registry:

```
sudo microk8s enable registry
```

The following commands, replacing `version` with the rock version, packs the OCI image and pushes it to the MicroK8s registry:

```bash
cd insights_rock
rockcraft pack
rockcraft.skopeo --insecure-policy copy --dest-tls-verify=false oci-archive:ubuntu-insights-server_<version>_amd64.rock
 docker://localhost:32000/ubuntu-insights-server:latest
```

Then, from the root of the git repository, build the charm

```bash
charmcraft pack
```

### About the test suite

The project includes a comprehensive test suite made of unit and integration tests. All the tests must pass before the review is considered. If you have troubles with the test suite, feel free to mention it on your PR description.

Tox is used for managing tests environments.

```bash
uv tool install --python-preference only-managed tox --with tox-uv
```

The test suite must pass before merging the PR to our main branch. Any new feature, change or fix must be covered by corresponding tests.

#### Basic Tests

There are some pre-configured environments that can be used for linting and formatting code when you're preparing contributions to the charm:

- `tox`: Executes all the basic checks and tests (`lint`, `unit`, and `static`).
- `tox -e fmt`: Runs formatting using `ruff`.
- `tox -e lint`: Runs a range of static code analysis to check the code.
- `tox -e static`: Runs other checks such as `pyright`.

#### Integration Tests

Integration tests are driven by [charm-ci](https://github.com/canonical/charm-ci) (`opcli`), using the same flow locally and in CI. The build plan is in `artifacts.yaml`, the test environment in `concierge.yaml`, and the test orchestration in `spread.yaml`.

Install `opcli` and its tooling:

```bash
uv tool install "opcli[cli] @ git+https://github.com/canonical/charm-ci.git@v1.0.1"
opcli install all
```

Build the rock and charm (writes `build/artifacts.build.yaml`):

```bash
opcli artifacts build
```

Then run the tests either in an isolated LXD VM through spread:

```bash
opcli spread run
```

Or directly on the host, which is faster for iteration:

```bash
opcli env provision
opcli artifacts push-images --missing-registry deploy
opcli pytest run
```

The built charm and rock image are injected into the tests through the `charm_path` and `resource_images` fixtures. To test pre-built artifacts instead, pass them to tox directly:

```bash
tox -e integration -- \
  --charm-file ubuntu-insights-server-k8s=./ubuntu-insights-server-k8s_amd64.charm \
  --resource-image ubuntu-insights-server-image=localhost:32000/ubuntu-insights-server:latest
```

## Contributor License Agreement

It is required to sign the [Contributor License Agreement](https://ubuntu.com/legal/contributors) in order to contribute to this project.

An automated test is executed on PRs to check if it has been accepted.

This project is covered by [GPL-3.0](LICENSE).

## Getting Help

Join us in the [Ubuntu Community](https://discourse.ubuntu.com/c/desktop/8) and post your question there with a descriptive tag.
