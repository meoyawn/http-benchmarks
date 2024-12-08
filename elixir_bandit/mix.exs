defmodule ElixirBandit.MixProject do
  use Mix.Project

  def project do
    [
      app: :elixir_bandit,
      version: "0.1.0",
      elixir: "~> 1.17",
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  # Run "mix help compile.app" to learn about applications.
  def application do
    [
      extra_applications: [:logger],
      mod: {ElixirBandit.Application, []}
    ]
  end

  # Run "mix help deps" to learn about dependencies.
  defp deps do
    [
      # {:dep_from_hexpm, "~> 0.3.0"},
      # {:dep_from_git, git: "https://github.com/elixir-lang/my_dep.git", tag: "0.1.0"}
      {:bandit, "~> 1.6.1"},
      {:exqlite, "~> 0.27.1"},
      {:validate, "~> 1.3.1"}
    ]
  end
end
