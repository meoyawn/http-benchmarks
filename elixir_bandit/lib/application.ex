defmodule CachedConn do
  @enforce_keys [:conn, :cache]
  defstruct [:conn, :cache]

  @type t :: %__MODULE__{
          conn: Exqlite.Sqlite3.db(),
          cache: %{String.t() => Exqlite.Sqlite3.statement()}
        }

  alias Exqlite.Sqlite3

  @spec prepare(t(), String.t()) ::
          {:ok, t(), Sqlite3.statement()} | {:error, Sqlite3.reason()}
  def prepare(cc, sql) do
    case Map.get(cc.cache, sql) do
      nil ->
        case Sqlite3.prepare(cc.conn, sql) do
          {:ok, stmt} ->
            {:ok, %{cc | cache: Map.put(cc.cache, sql, stmt)}, stmt}

          {:error, reason} ->
            {:error, reason}
        end

      stmt ->
        {:ok, cc, stmt}
    end
  end

  def immediate(cc, func) do
    with {:ok, state, begin} <- prepare(cc, "BEGIN IMMEDIATE TRANSACTION"),
         :done <- Sqlite3.step(state.conn, begin) do
      with {:ok, state, result} <- func.(state),
           {:ok, state, commit} <- prepare(state, "COMMIT"),
           :done <- Sqlite3.step(state.conn, commit) do
        {:ok, state, result}
      else
        {:error, reason} ->
          with {:ok, state, rollback} <- prepare(cc, "ROLLBACK"),
               :done <- Sqlite3.step(state.conn, rollback) do
            {:error, reason}
          else
            {:error, reason} -> {:error, reason}
          end
      end
    else
      {:error, reason} ->
        {:error, reason}
    end
  end
end

defmodule DbWriter do
  use GenServer

  alias Exqlite.Sqlite3

  @open_pragmas """
  PRAGMA journal_mode = wal;
  PRAGMA synchronous = normal;
  PRAGMA foreign_keys = on;
  PRAGMA busy_timeout = 10000;

  PRAGMA optimize = 0x10002;
  """

  @insert_user "INSERT OR IGNORE INTO users (email) VALUES (?)"

  @insert_post """
  INSERT INTO posts   (content,   user_id)
  SELECT              ?,          id
  FROM        users
  WHERE       email = ?
  RETURNING   id, user_id, content, created_at, updated_at
  """

  @impl true
  @spec init(path :: String.t()) :: {:ok, CachedConn.t()} | {:stop, Sqlite3.reason()}
  def init(path) do
    with {:ok, conn} <- Sqlite3.open(path),
         :ok <- Sqlite3.execute(conn, @open_pragmas) do
      {:ok, %CachedConn{conn: conn, cache: %{}}}
    else
      {:error, reason} -> {:stop, reason}
    end
  end

  @impl true
  def handle_call(request, _from, state) do
    case CachedConn.immediate(state, fn state ->
           with {:ok, state, iu} <- CachedConn.prepare(state, @insert_user),
                :ok <- Sqlite3.bind(iu, [request.email]),
                :done <- Sqlite3.step(state.conn, iu),
                {:ok, state, ip} <- CachedConn.prepare(state, @insert_post),
                :ok <- Sqlite3.bind(ip, [request.content, request.email]),
                {:row, [id, user_id, content, created_at, updated_at]} <-
                  Sqlite3.step(state.conn, ip) do
             {:reply,
              %{
                id: id,
                user_id: user_id,
                content: content,
                created_at: created_at,
                updated_at: updated_at
              }, state}
           else
             {:error, reason} -> {:reply, reason, state}
           end
         end) do
      {:ok, state, result} -> {:reply, result, state}
      {:error, reason} -> {:reply, reason, state}
    end
  end

  @impl true
  @spec terminate(Sqlite3.reason(), Sqlite3.db()) ::
          :ok | {:error, Sqlite3.reason()}
  def terminate(_reason, state) do
    with :ok <- Sqlite3.execute(state, "PRAGMA optimize"),
         :ok <- Sqlite3.close(state) do
      :ok
    else
      {:error, reason} -> {:error, reason}
    end
  end
end

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

  @spec on_valid(Plug.Conn.t(), data :: new_post()) :: Plug.Conn.t()
  defp on_valid(conn, data) do
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
