# Dotfiles
Backup, restore, and sync the prefs and settings for your toolbox. Your dotfiles might be the most important files on your machine!

## Install
```
git clone https://github.com/hkjersem/dotfiles.git && cd dotfiles && ./install
```

#### Dependencies
```
sudo easy_install pip && pip search yaml && pip install pyyaml
```

#### Fonts
* vscode: SF Mono & FiraCode
* iTerm: SourceCodePro

## Update
```
git pull origin master && ./install
```

## Applications & Settings

Fresh install:
```
source ~/.dotfiles/macos/install.sh
```

Update applications and settings:
```
source ~/.dotfiles/macos/update.sh
```

## Machine profile

The first `dotfiles-install` or `dotfiles-update` asks whether this is a `personal`
or `work` machine. The choice is saved in `~/.config/dotfiles/profile`,
outside the repository, so each machine keeps its own setting.

```sh
dotfiles-profile personal       # Set or change the profile
dotfiles-profile work
dotfiles-profile                # Show it (ask if unset)
dotfiles-profile --choose       # Ask again
# Before shell aliases are available:
bash ~/.dotfiles/scripts/machine-profile.sh personal
```

Personal and work profiles can install different tools. The profile only
controls automatic installation: installed tools are updated and synced on
either profile, regardless of which profile normally installs them. Changing
the profile does not uninstall anything or change directory-based Git identities.

Non-interactive installation and updates require a saved profile. The audit
reports a missing or invalid profile without prompting or changing it.
Install/update scripts export the saved choice as `DOTFILES_PROFILE` for
conditional entries in `Brewfile`. When running `brew bundle` manually, set
`DOTFILES_PROFILE=work` explicitly to include work-only packages.

## Git identity

Git uses your global personal identity by default. During installation,
`scripts/setup-git-local.sh` can configure a work identity for a directory,
with personal exceptions inside it. This is independent of the machine profile.

```sh
bash ~/.dotfiles/scripts/setup-git-local.sh
git whoami
```

The setup writes `~/.gitconfig_local`, `~/.gitconfig_local_work`, and
`~/.gitconfig_local_personal` as needed. These files stay outside the repository.
Re-running asks before replacing the managed block and preserves other settings
in `~/.gitconfig_local`. Declining work setup removes the managed directory
overrides, without deleting the separate identity files.

## Clean and copy projects

`wipe_clean` previews generated directories and cache files, then asks before
deleting them. Git metadata and submodule directories are preserved.
`copy_clean` copies a project without the same generated artifacts, using
macOS clone-on-write copies where supported. Existing destinations are never
overwritten.

```sh
wipe_clean
copy_clean ../project-copy
copy_clean --ignore-git --keep-name ../copies
copy_clean --zip
copy_clean --zip ../project ../project-clean.zip
```

A single copy argument is the destination; the source defaults to the current
directory. `--keep-name` requires an existing destination directory.
ZIP archives always include the source folder name; without a destination,
the archive is created inside the source. `--ignore-git` excludes `.git`
directories; otherwise Git metadata is copied too.

#### iTerm
To install preferences, open settings and enable "*Load preferences from a custom folder or URL*" and point it to `~/.dotfiles/iterm/com.googlecode.iterm2.plist`
