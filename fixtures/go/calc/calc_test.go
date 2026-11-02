package calc

import (
	"errors"
	"testing"
)

func TestMean(t *testing.T) {
	got, err := Mean([]float64{1, 2, 3})
	if err != nil || got != 2 {
		t.Fatalf("Mean = %v, %v; want 2, nil", got, err)
	}
}

func TestMeanEmpty(t *testing.T) {
	if _, err := Mean(nil); !errors.Is(err, ErrEmpty) {
		t.Fatalf("err = %v; want ErrEmpty", err)
	}
}
