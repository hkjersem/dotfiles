# User configuration
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Homebrew
export PATH=/opt/homebrew/bin:$PATH

# Set language environment
export LANG=en_US.UTF-8
export LC_ALL=en_US.utf-8

# Give detailed report for all commands taking more than 5 seconds
export REPORTTIME=5
export TIMEFMT='
> %J

  | Time:   %*E total time, %U user time, %S kernel time
  | Disk:   %F major page faults (pages loaded from disk)
  | System: %P CPU used, %M KB max memory used'
