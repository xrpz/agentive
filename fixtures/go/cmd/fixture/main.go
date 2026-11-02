package main

import (
	"fmt"

	"example.com/agentive-fixture-go/calc"
)

func main() {
	m, _ := calc.Mean([]float64{1, 2, 3})
	fmt.Println(m)
}
