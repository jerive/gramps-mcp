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
as a pre-release.

The documentation does not mention the latest version: its install snippets
follow `master` and say how to pin a tag. It cannot show the latest tag
automatically, because GitHub Pages treats a second deployment of the same
commit as a duplicate, and a tag points at a commit that was already deployed
when it was merged.

Edit the generated notes on the release page if a change deserves a warning,
such as a breaking one.
