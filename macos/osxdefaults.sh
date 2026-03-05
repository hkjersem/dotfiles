#!/usr/bin/env bash
# Usage: osxdefaults.sh [--dns]
#   --dns  Also set Cloudflare DNS on all network interfaces

SET_DNS=false
for arg in "$@"; do
  [[ "$arg" == "--dns" ]] && SET_DNS=true
done

# Close any open System Settings/Preferences panes, to prevent them from overriding settings we're about to change
osascript -e 'tell application "System Settings" to quit' 2>/dev/null
osascript -e 'tell application "System Preferences" to quit' 2>/dev/null

# Ask for the administrator password upfront (if not already authenticated)
sudo -n true 2>/dev/null || { echo "Some settings require administrator access. Please enter your password:"; sudo -v; }

# Keep-alive: update existing `sudo` time stamp until the script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

###############################################################################
# General UI/UX                                                               #
###############################################################################

# Menu bar Icons
defaults write com.apple.controlcenter "NSStatusItem VisibleCC WiFi" -bool true
defaults write com.apple.controlcenter "NSStatusItem VisibleCC Battery" -bool true
defaults write com.apple.controlcenter "NSStatusItem VisibleCC Bluetooth" -bool true
defaults write com.apple.controlcenter "NSStatusItem VisibleCC Sound" -bool true
defaults write com.apple.controlcenter "NSStatusItem VisibleCC NowPlaying" -bool true
defaults write com.apple.controlcenter "NSStatusItem VisibleCC Clock" -bool true
defaults write com.apple.menuextra.clock ShowDayOfWeek -bool true
defaults write com.apple.menuextra.clock ShowDate -bool false

# Expand save panel by default
defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode -bool true
defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode2 -bool true

# Expand print panel by default
defaults write NSGlobalDomain PMPrintingExpandedStateForPrint -bool true
defaults write NSGlobalDomain PMPrintingExpandedStateForPrint2 -bool true

# Save to disk (not to iCloud) by default
defaults write NSGlobalDomain NSDocumentSaveNewDocumentsToCloud -bool false

# Disable the “Are you sure you want to open this application?” dialog
defaults write com.apple.LaunchServices LSQuarantine -bool false

# Remove duplicates in the “Open With” menu (also see `lscleanup` alias)
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -r -domain local -domain system -domain user

# Disable automatic capitalization as it’s annoying when typing code
defaults write NSGlobalDomain NSAutomaticCapitalizationEnabled -bool false

# Disable smart dashes as they’re annoying when typing code
defaults write NSGlobalDomain NSAutomaticDashSubstitutionEnabled -bool false

# Disable automatic period substitution as it’s annoying when typing code
defaults write NSGlobalDomain NSAutomaticPeriodSubstitutionEnabled -bool false

# Disable smart quotes as they’re annoying when typing code
defaults write NSGlobalDomain NSAutomaticQuoteSubstitutionEnabled -bool false

# Disable auto-correct
defaults write NSGlobalDomain NSAutomaticSpellingCorrectionEnabled -bool false

# Trackpad: enable tap to click for this user and for the login screen
defaults write com.apple.driver.AppleBluetoothMultitouch.trackpad Clicking -bool true
defaults -currentHost write NSGlobalDomain com.apple.mouse.tapBehavior -int 1
defaults write NSGlobalDomain com.apple.mouse.tapBehavior -int 1

###############################################################################
# Finder                                                                      #
###############################################################################

# Finder: show status bar
defaults write com.apple.finder ShowStatusBar -bool true

# Disable the warning when changing a file extension
defaults write com.apple.finder FXEnableExtensionChangeWarning -bool false

# Avoid creating .DS_Store files on network or USB volumes
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true
defaults write com.apple.desktopservices DSDontWriteUSBStores -bool true

# Enable snap-to-grid for icons on the desktop and in other icon views
/usr/libexec/PlistBuddy -c "Set :DesktopViewSettings:IconViewSettings:arrangeBy grid" ~/Library/Preferences/com.apple.finder.plist
/usr/libexec/PlistBuddy -c "Set :FK_StandardViewSettings:IconViewSettings:arrangeBy grid" ~/Library/Preferences/com.apple.finder.plist
/usr/libexec/PlistBuddy -c "Set :StandardViewSettings:IconViewSettings:arrangeBy grid" ~/Library/Preferences/com.apple.finder.plist

# Show the ~/Library folder
chflags nohidden ~/Library

# Show the /Volumes folder
sudo chflags nohidden /Volumes

# Expand the following File Info panes
defaults write com.apple.finder FXInfoPanesExpanded -dict \
    General -bool true \
    OpenWith -bool true \
    Privileges -bool true

# Make the titlebar proxy icon appear immediately
defaults write -g NSToolbarTitleViewRolloverDelay -float 0

# Finder: default to column view
defaults write com.apple.finder FXPreferredViewStyle -string "clmv"

# Finder: search current folder by default
defaults write com.apple.finder FXDefaultSearchScope -string "SCcf"

###############################################################################
# Dock and Mission Control                                                    #
###############################################################################

# Set the icon size of Dock items
defaults write com.apple.dock tilesize -int 48

# Change minimize/maximize window effect
defaults write com.apple.dock mineffect -string "scale"

# Automatically hide and show the Dock
defaults write com.apple.dock autohide -bool true

# Remove the auto-hiding Dock delay
defaults write com.apple.dock autohide-delay -float 0

# Speed up Mission Control, Launchpad and Dock animations
defaults write com.apple.dock expose-animation-duration -float 0.1
defaults write com.apple.dock springboard-show-duration -float 0.1
defaults write com.apple.dock springboard-hide-duration -float 0.1
defaults write com.apple.dock autohide-time-modifier -float 0.1

# Don't show recent apps in the Dock
defaults write com.apple.dock show-recents -bool false

###############################################################################
# Safari & WebKit                                                             #
###############################################################################

# Enable the Develop menu in Safari
defaults write com.apple.Safari.SandboxBroker ShowDevelopMenu -bool true

# Add a context menu item for showing the Web Inspector in web views
defaults write NSGlobalDomain WebKitDeveloperExtras -bool true

###############################################################################
# Terminal & iTerm 2                                                          #
###############################################################################

# Only use UTF-8 in Terminal.app
defaults write com.apple.terminal StringEncodings -array 4

# Enable Secure Keyboard Entry in Terminal.app - See: https://security.stackexchange.com/a/47786/8918
defaults write com.apple.terminal SecureKeyboardEntry -bool true

# Don’t display the annoying prompt when quitting iTerm
defaults write com.googlecode.iterm2 PromptOnQuit -bool false

###############################################################################
# Activity Monitor                                                            #
###############################################################################

# Show the main window when launching Activity Monitor
defaults write com.apple.ActivityMonitor OpenMainWindow -bool true

# Sort Activity Monitor results by CPU usage
defaults write com.apple.ActivityMonitor SortColumn -string "CPUUsage"
defaults write com.apple.ActivityMonitor SortDirection -int 0

###############################################################################
# Mac App Store                                                               #
###############################################################################

# Enable the automatic update check
defaults write com.apple.SoftwareUpdate AutomaticCheckEnabled -bool true

# Check for software updates daily, not just once per week
defaults write com.apple.SoftwareUpdate ScheduleFrequency -int 1

# Download newly available updates in background
defaults write com.apple.SoftwareUpdate AutomaticDownload -int 1

# Install System data files & security updates
defaults write com.apple.SoftwareUpdate CriticalUpdateInstall -int 1

# Turn on app auto-update
defaults write com.apple.commerce AutoUpdate -bool true

###############################################################################
# Misc                                                                        #
###############################################################################

# Prevent Photos from opening automatically when devices are plugged in
defaults -currentHost write com.apple.ImageCapture disableHotPlug -bool true

# Screenshots: save to Downloads folder
defaults write com.apple.screencapture location -string "${HOME}/Downloads"

# Disable Crash Reporter dialogs
defaults write com.apple.CrashReporter DialogType -string "none"

# Prevent Time Machine from prompting to use new hard drives as backup volume
defaults write com.apple.TimeMachine DoNotOfferNewDisksForBackup -bool true

###############################################################################
# Optional settings                                                           #
###############################################################################

# Use Cloudflare DNS on all active network services (pass --dns to enable)
if $SET_DNS; then
    while IFS= read -r service; do
        networksetup -setdnsservers "$service" 1.1.1.1 1.0.0.1 2606:4700:4700::1111 2606:4700:4700::1001
    done < <(networksetup -listallnetworkservices | tail -n +2 | grep -v '^\*\|Thunderbolt')
    sudo killall -HUP mDNSResponder;sudo killall mDNSResponderHelper;sudo dscacheutil -flushcache
fi

###############################################################################
# Kill affected applications                                                  #
###############################################################################

for app in "Activity Monitor" \
	"cfprefsd" \
	"Dock" \
	"Finder" \
	"Photos" \
	"Safari" \
	"ControlCenter" \
	"SystemUIServer"; do
	killall "${app}" &> /dev/null
done

echo "Done. Note that some of these changes require a logout/restart to take effect."
