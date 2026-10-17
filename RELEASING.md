# Releasing

The version comes from the git tag, so a release is a tag on `master`:

```sh
git switch master && git pull
git tag -a v0.6.0 -m "v0.6.0"
git push origin v0.6.0
```

Pushing the tag runs `.github/workflows/release.yml`: the tests first, then a
check that the tag is on `master`, then a GitHub release with notes generated
from the merged pull requests. A tag with a hyphen (`v0.6.0-rc1`) is published
as a pre-release. The documentation site is rebuilt and shows the new tag in its
install snippets.

Edit the generated notes on the release page if a change deserves a warning,
such as a breaking one.
