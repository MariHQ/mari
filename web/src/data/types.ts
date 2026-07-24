/* The contract between this app and the component library.
 *
 * A page in `@mari-design/components` is a pure presenter: it takes
 * `{ data, loading, error, mobile }` and renders exactly what it is given.
 * An *adapter* is the other half of that contract — a hook that runs the
 * GraphQL query the page needs and maps the response onto the page's exported
 * `XxxData` type. One adapter per page, in `src/data/<page>.ts`.
 *
 * Everything the app knows about the backend's shape lives in adapters. The
 * library knows nothing about GraphQL; the app knows nothing about layout. */

export type PageData<TData> = {
  /** Fully-formed page data. Never partial, never invented — when the query
   *  has not answered yet this is the adapter's own `EMPTY` value, and
   *  `loading` is true so the page renders its skeleton instead. */
  data: TData;
  loading: boolean;
  error: string | null;
};

/** A page adapter: a React hook returning the props the page component takes. */
export type Adapter<TData = unknown> = () => PageData<TData>;
