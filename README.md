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

#### iTerm
To install preferences, open settings and enable "*Load preferences from a custom folder or URL*" and point it to `~/.dotfiles/iterm/com.googlecode.iterm2.plist`
