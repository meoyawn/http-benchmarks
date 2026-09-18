using System.Threading.Channels;

internal sealed class DatabaseWriter : IDisposable
{
    private sealed record Call(NewPost Body, TaskCompletionSource<Post> Reply);
    private readonly Channel<Call> requests = Channel.CreateBounded<Call>(new BoundedChannelOptions(1024)
    { SingleReader = true, AllowSynchronousContinuations = false });
    private readonly Thread thread;
    private readonly TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private Exception? failure;

    public DatabaseWriter(string path)
    {
        thread = new Thread(() => Run(path)) { Name = "SQLite writer" };
        thread.Start();
        ready.Task.GetAwaiter().GetResult();
    }

    public async ValueTask<Post> WriteAsync(NewPost body)
    {
        var reply = new TaskCompletionSource<Post>(TaskCreationOptions.RunContinuationsAsynchronously);
        await requests.Writer.WriteAsync(new Call(body, reply));
        return await reply.Task;
    }

    private void Run(string path)
    {
        try
        {
            using var database = new Database(path);
            ready.SetResult();
            while (requests.Reader.WaitToReadAsync().AsTask().GetAwaiter().GetResult())
                while (requests.Reader.TryRead(out var call))
                    // Complete this request immediately after its own COMMIT.
                    // RunContinuationsAsynchronously keeps HTTP work off this thread;
                    // there is no reply collection, timer, polling or grouped wake-up.
                    try { call.Reply.SetResult(database.Insert(call.Body)); }
                    catch (Exception error) { call.Reply.SetException(error); }
        }
        catch (Exception error)
        {
            failure = error;
            ready.TrySetException(error);
            requests.Writer.TryComplete(error);
            while (requests.Reader.TryRead(out var call)) call.Reply.TrySetException(error);
        }
    }

    public void Dispose()
    {
        requests.Writer.TryComplete();
        thread.Join();
        if (failure is not null) throw new InvalidOperationException("SQLite writer failed", failure);
    }
}
