defmodule ElixirBanditTest do
  use ExUnit.Case
  doctest ElixirBandit

  test "greets the world" do
    assert ElixirBandit.hello() == :world
  end
end
