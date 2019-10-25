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

#### iTerm
To install preferences, open settings and enable "*Load preferences from a custom folder or URL*" and point it to `~/.dotfiles/iterm/com.googlecode.iterm2.plist`
