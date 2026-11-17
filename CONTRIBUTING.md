# Contributing to ShareHub

Thank you for helping. Bug reports, test results from real deployments and pull requests are all welcome.

## Development setup

```bash
git clone https://github.com/Ancyent/cockpit_sharehub.git
cd cockpit_sharehub
sudo apt install cockpit samba nfs-kernel-server proftpd-core proftpd-mod-crypto \
                 python3-pytest python3-pyflakes nodejs debhelper

# Run the plugin straight from the checkout (symlinks):
sudo ln -sfn "$PWD/frontend" /usr/share/cockpit/sharehub
sudo mkdir -p /usr/lib/sharehub
sudo ln -sfn "$PWD/backend/sharehub" /usr/lib/sharehub/sharehub
sudo ln -sfn "$PWD/backend/sharehub-helper" /usr/lib/sharehub/sharehub-helper
sudo /usr/lib/sharehub/sharehub-helper bootstrap
```

- **Frontend changes:** reload the browser to see them.
- **Backend changes:** take effect on the next request.

## Before you open a pull request

```bash
make lint
make test
```

- **Language:** code, comments, UI text and documentation are in English.
- **No build step:** the frontend is plain JavaScript; keep it that way.
- **Controls:** use the custom components in `frontend/js/controls.js`. Do not use native `<select>`, checkboxes or `type="number"`.
- **Helper input:** every new helper command validates its input and returns errors written for people.
- **Pure logic:** new logic that does not need the system belongs in a unit test.
- **Documentation:** update `docs/` when behaviour changes.

## Developer Certificate of Origin

Contributions are accepted under the [DCO](https://developercertificate.org/). Sign off each commit:

```bash
git commit -s -m "Describe the change"
```

By signing off you certify that you wrote the change, or have the right to submit it under GPL-3.0-or-later.

## Reporting bugs

Please include:
- the Ubuntu or Debian version, plus the Samba and Cockpit versions;
- the exact error message shown in ShareHub;
- the output of `echo '{}' | sudo /usr/lib/sharehub/sharehub-helper status` (remove anything private).
