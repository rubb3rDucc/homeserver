package main

import (
	"fmt"

	"golang.org/x/sys/unix"
)

// checkDiskUsage returns the usage percentage of the filesystem at the given path.
func checkDiskUsage(path string) (float64, error) {
	var stat unix.Statfs_t
	if err := unix.Statfs(path, &stat); err != nil {
		return 0, fmt.Errorf("statfs %s: %w", path, err)
	}

	total := stat.Blocks * uint64(stat.Bsize)
	free := stat.Bfree * uint64(stat.Bsize)
	used := total - free
	usagePercent := float64(used) / float64(total) * 100

	return usagePercent, nil
}
