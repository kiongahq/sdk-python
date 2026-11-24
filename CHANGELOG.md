# Changelog

## [0.3.3](https://github.com/kiongahq/sdk-python/compare/v0.3.2...v0.3.3) (2026-11-24)


### Features

* follow application logs, kionga apps logs --follow, scaling on deploy_agent_version ([#62](https://github.com/kiongahq/sdk-python/issues/62)) ([ca385cf](https://github.com/kiongahq/sdk-python/commit/ca385cff58d0a00ce209d4aa69fe7fabcfb107de))
* resume followed logs from the gateway's per-pod cursor ([9e5d2fd](https://github.com/kiongahq/sdk-python/commit/9e5d2fd86386d5ad8ca4604b6cbe70108e1b68f8))
* run_as_user on applications and functions, kept across updates ([cc58eae](https://github.com/kiongahq/sdk-python/commit/cc58eae654988775788e92ebe8734e9387074a8b))
* visibility and labels on deploy_function, and the gateway's 45-character name limit ([f743685](https://github.com/kiongahq/sdk-python/commit/f7436850b2fa265e02f8fae3545644de43b9fee6))

## [0.3.2](https://github.com/kiongahq/sdk-python/compare/v0.3.1...v0.3.2) (2026-11-23)


### Features

* applications client and CLI, model deploy status, agent and function scaling ([#61](https://github.com/kiongahq/sdk-python/issues/61)) ([4ce6a93](https://github.com/kiongahq/sdk-python/commit/4ce6a932f9865da7279b68f63a7c2f58745a4dc6))


### Documentation

* turn eda.ipynb into a model promotion review ([#57](https://github.com/kiongahq/sdk-python/issues/57)) ([6d55706](https://github.com/kiongahq/sdk-python/commit/6d55706551d435d93377ef0a80ca366472d88e45))


### Build and dependencies

* limit the source distribution to a declared file list ([#60](https://github.com/kiongahq/sdk-python/issues/60)) ([5af8ab8](https://github.com/kiongahq/sdk-python/commit/5af8ab8a5a6b6296ec48a0d92fd5740f2a98ebab))

## [0.3.1](https://github.com/kiongahq/sdk-python/compare/v0.3.0...v0.3.1) (2026-11-11)


### Features

* typed errors, safe retries and agent teams ([#53](https://github.com/kiongahq/sdk-python/issues/53)) ([5c690b2](https://github.com/kiongahq/sdk-python/commit/5c690b2b58cae5de8a0a108caa80b193b96a41c2))

## [0.3.0](https://github.com/kiongahq/sdk-python/compare/v0.2.0...v0.3.0) (2026-11-10)


### ⚠ BREAKING CHANGES

* the distribution, module, client class, environment variables and headers are renamed from mlaiops to kionga.

### Features

* **client:** Agent Designer designs, runs, evaluations, versions and deploys ([a5440e6](https://github.com/kiongahq/sdk-python/commit/a5440e646e8436d860eeb9f24d0df3bd9e7dac01))
* **client:** approvals, case reviews, flow versions and agent operations ([d0ebb11](https://github.com/kiongahq/sdk-python/commit/d0ebb1125204fbd5bf99273ae86a5a834a421c47))
* **release:** publish verified SDK distributions to PyPI ([a166dfc](https://github.com/kiongahq/sdk-python/commit/a166dfc0c0ed8bace69f6ce54d10d6137b7ec429))
* **release:** publish verified SDK packages to PyPI ([0bfbf6f](https://github.com/kiongahq/sdk-python/commit/0bfbf6fbea948f8bf81d4034cc001b42abf60240))
* rename the package to kionga ([#51](https://github.com/kiongahq/sdk-python/issues/51)) ([403a770](https://github.com/kiongahq/sdk-python/commit/403a7706a55fa57783f90d9223379ea28686949f))


### Fixes

* **release:** hold PyPI without blocking GitHub packages ([563b307](https://github.com/kiongahq/sdk-python/commit/563b307f8fbbde2f2d214f03e726f798db84d5bd))
* **release:** pause PyPI without blocking GitHub SDK packages ([64b5d7e](https://github.com/kiongahq/sdk-python/commit/64b5d7eaa937d0335ca96bca8ef275e45bf83334))
* **sdk:** honor gateway URL in notebook environment ([d984ada](https://github.com/kiongahq/sdk-python/commit/d984ada02a88f3e683a1b82037b89e11082b0e69))
* **sdk:** normalize empty gateway collections ([ff30fae](https://github.com/kiongahq/sdk-python/commit/ff30faed9d59f6dfe579bcc28c53c47cceee285b))
* **security:** token transport check and user-scoped agent memory ([#48](https://github.com/kiongahq/sdk-python/issues/48)) ([0cda6ca](https://github.com/kiongahq/sdk-python/commit/0cda6cae236952b5156ee9828103c157af464a40)), closes [#24](https://github.com/kiongahq/sdk-python/issues/24)


### Documentation

* product README, CONTRIBUTING and ARCHITECTURE ([a80b3a7](https://github.com/kiongahq/sdk-python/commit/a80b3a7ee080fd8dd6cc8cfe2082c3d39bd01290))
* **release:** align package installation and image references ([4e0b243](https://github.com/kiongahq/sdk-python/commit/4e0b243fc90e6b51eb30d3c25a9d8dd3643f2b5b))
* **sdk:** explain tokenless in-platform access ([fd0b1a7](https://github.com/kiongahq/sdk-python/commit/fd0b1a707df49200f2b44ea9a44415333697dddc))
* **security:** clarify sole-maintainer release review ([daca018](https://github.com/kiongahq/sdk-python/commit/daca0181db9557d5dc98471c9a8aed3736adbba1))


### Maintenance

* ignore local tooling and secret files ([ce2866c](https://github.com/kiongahq/sdk-python/commit/ce2866c9611ceb6d945225803f8c25bd4ea7f7e6))
* license repository under MIT ([3aaf301](https://github.com/kiongahq/sdk-python/commit/3aaf301fb2ee9d6be63ed637faaf0961ce1d0727))

## Changelog

Reviewed release pull requests add versioned changes here.
Existing tags and releases remain available in [GitHub](https://github.com/kiongahq/sdk-python/releases).
