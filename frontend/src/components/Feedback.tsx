export function Feedback({
  error,
  message,
  loading,
}: {
  error?: string;
  message?: string;
  loading?: boolean;
}) {
  return (
    <>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="success" role="status">
          {message}
        </p>
      )}
      {loading && (
        <p className="loading" role="status">
          Загрузка…
        </p>
      )}
    </>
  );
}
