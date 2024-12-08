defmodule HttpPost do
  import Plug.Conn

  @rules %{
    "email" => [
      required: true,
      type: :string,
      regex: ~r/^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/
    ],
    "content" => [
      required: true,
      type: :string,
      regex: ~r/^.+$/
    ]
  }

  @type new_post() :: %{
          email: String.t(),
          content: String.t()
        }

  @type post() :: %{
          id: integer,
          user_id: integer,
          content: String.t(),
          created_at: integer,
          updated_at: integer
        }

  @spec on_valid(Plug.Conn.t(), new_post()) :: Plug.Conn.t()
  def on_valid(conn, data) do
    conn
    |> put_resp_header("content-type", "application/json")
    |> send_resp(200, :json.encode(data))
  end

  @spec http_post(Plug.Conn.t()) :: Plug.Conn.t()
  def http_post(conn) do
    case Validate.validate(conn.body_params, @rules) do
      {:ok, data} ->
        on_valid(conn, data)

      {:error, errors} ->
        conn
        |> put_resp_header("content-type", "application/json")
        |> send_resp(400, :json.encode(%{errors: errors |> Enum.map(&Map.from_struct(&1))}))
    end
  end
end

defmodule Router do
  use Plug.Router

  plug(:match)

  plug(Plug.Parsers,
    parsers: [:json],
    json_decoder: {:json, :decode, []}
  )

  plug(:dispatch)

  post "/posts" do
    HttpPost.http_post(conn)
  end

  # no validation
  post "/echo" do
    conn
    |> put_resp_header("content-type", "application/json")
    |> send_resp(200, :json.encode(conn.body_params))
  end

  match _ do
    send_resp(conn, 404, "oops")
  end
end

defmodule ElixirBandit.Application do
  # See https://hexdocs.pm/elixir/Application.html
  # for more information on OTP Applications
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Bandit, plug: Router, ip: {:local, "/tmp/benchmark.sock"}, port: 0}
    ]

    # See https://hexdocs.pm/elixir/Supervisor.html
    # for other strategies and supported options
    opts = [strategy: :one_for_one, name: ElixirBandit.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
