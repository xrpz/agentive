// Package calc is the agentive self-test fixture for Go.
package calc

import "errors"

// ErrEmpty is returned for an empty input.
var ErrEmpty = errors.New("calc: empty input")

// Mean returns the arithmetic mean of xs.
func Mean(xs []float64) (float64, error) {
	if len(xs) == 0 {
		return 0, ErrEmpty
	}
	var sum float64
	for _, x := range xs {
		sum += x
	}
	return sum / float64(len(xs)), nil
}
