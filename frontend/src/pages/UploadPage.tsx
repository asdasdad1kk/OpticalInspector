import {
  useMemo,
  useState,
} from "react";

import {
  CheckCircle2,
  Expand,
  FileCode2,
  ImageIcon,
  LoaderCircle,
  RefreshCw,
  UploadCloud,
} from "lucide-react";

import {
  drawingProcessedImageUrl,
  uploadDrawing,
} from "../lib/api";

import type {
  UploadResponse,
} from "../types/api";


export default function UploadPage() {
  const [partNo, setPartNo] =
    useState("");

  const [revision, setRevision] =
    useState("");

  const [
    description,
    setDescription,
  ] = useState("");

  const [file, setFile] =
    useState<File>();

  const [loading, setLoading] =
    useState(false);

  const [previewLoading, setPreviewLoading] =
    useState(false);

  const [previewError, setPreviewError] =
    useState(false);

  const [error, setError] =
    useState<string>();

  const [result, setResult] =
    useState<UploadResponse>();


  const previewUrl = useMemo(() => {
    if (!result?.drawing_id) {
      return undefined;
    }

    return drawingProcessedImageUrl(
      result.drawing_id,
    );
  }, [result]);


  function handleFileChange(
    event: React.ChangeEvent<HTMLInputElement>,
  ) {
    const selectedFile =
      event.target.files?.[0];

    setError(undefined);
    setResult(undefined);
    setPreviewError(false);

    if (!selectedFile) {
      setFile(undefined);
      return;
    }

    if (
      !selectedFile.name
        .toLowerCase()
        .endsWith(".dxf")
    ) {
      setFile(undefined);

      setError(
        "El archivo seleccionado debe ser DXF.",
      );

      event.target.value = "";
      return;
    }

    setFile(selectedFile);
  }


  async function submit(
    event: React.FormEvent,
  ) {
    event.preventDefault();

    if (!file) {
      setError(
        "Selecciona un archivo DXF.",
      );
      return;
    }

    if (!partNo.trim()) {
      setError(
        "Captura el número de parte.",
      );
      return;
    }

    if (!revision.trim()) {
      setError(
        "Captura la revisión.",
      );
      return;
    }

    try {
      setLoading(true);
      setError(undefined);
      setResult(undefined);
      setPreviewError(false);
      setPreviewLoading(true);

      const response =
        await uploadDrawing({
          dxf: file,
          partNo,
          revision,
          description,
        });

      setResult(response);

    } catch (error) {
      setPreviewLoading(false);

      setError(
        error instanceof Error
          ? error.message
          : "No fue posible cargar el drawing.",
      );

    } finally {
      setLoading(false);
    }
  }


  function resetForm() {
    setPartNo("");
    setRevision("");
    setDescription("");
    setFile(undefined);
    setResult(undefined);
    setError(undefined);
    setPreviewError(false);
    setPreviewLoading(false);
  }


  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {/* Header */}

      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-emerald-400">
            Configuración de ingeniería
          </p>

          <h1 className="mt-1 text-3xl font-semibold tracking-tight">
            Registrar drawing
          </h1>

          <p className="mt-2 max-w-2xl text-sm text-zinc-500">
            Sube el DXF para generar los ROIs,
            clasificar componentes y almacenar
            el drawing y su preview en la base
            de datos.
          </p>
        </div>

        {(file || result) && (
          <button
            type="button"
            onClick={resetForm}
            disabled={loading}
            className="
              inline-flex items-center justify-center
              gap-2 rounded-lg border border-zinc-700
              bg-zinc-900 px-4 py-2 text-sm
              font-medium text-zinc-300
              transition hover:bg-zinc-800
              disabled:opacity-40
            "
          >
            <RefreshCw size={16} />

            Nuevo drawing
          </button>
        )}
      </div>

      {/* Error */}

      {error && (
        <div className="
          rounded-xl border border-red-500/30
          bg-red-500/10 px-4 py-3
          text-sm text-red-300
        ">
          {error}
        </div>
      )}

      {/* Form */}

      <form
        onSubmit={submit}
        className="
          rounded-2xl border border-zinc-800
          bg-zinc-900/60 p-6 shadow-2xl
        "
      >
        <div className="grid gap-4 md:grid-cols-2">
          <label>
            <span className="
              mb-1.5 block text-xs
              font-medium text-zinc-400
            ">
              Número de parte
            </span>

            <input
              required
              value={partNo}
              onChange={(event) =>
                setPartNo(event.target.value)
              }
              placeholder="62P2554000A273"
              className="
                w-full rounded-lg border
                border-zinc-700 bg-zinc-950
                px-3 py-2.5 text-sm
                outline-none
                placeholder:text-zinc-700
                focus:border-emerald-500
                focus:ring-2
                focus:ring-emerald-500/15
              "
            />
          </label>

          <label>
            <span className="
              mb-1.5 block text-xs
              font-medium text-zinc-400
            ">
              Revisión
            </span>

            <input
              required
              value={revision}
              onChange={(event) =>
                setRevision(event.target.value)
              }
              placeholder="A"
              className="
                w-full rounded-lg border
                border-zinc-700 bg-zinc-950
                px-3 py-2.5 text-sm
                outline-none
                placeholder:text-zinc-700
                focus:border-emerald-500
                focus:ring-2
                focus:ring-emerald-500/15
              "
            />
          </label>

          <label className="md:col-span-2">
            <span className="
              mb-1.5 block text-xs
              font-medium text-zinc-400
            ">
              Descripción
            </span>

            <textarea
              value={description}
              onChange={(event) =>
                setDescription(
                  event.target.value,
                )
              }
              rows={3}
              placeholder="Descripción del panel o drawing"
              className="
                w-full resize-none rounded-lg
                border border-zinc-700
                bg-zinc-950 px-3 py-2.5
                text-sm outline-none
                placeholder:text-zinc-700
                focus:border-emerald-500
                focus:ring-2
                focus:ring-emerald-500/15
              "
            />
          </label>

          <label className="
            md:col-span-2 flex cursor-pointer
            flex-col items-center
            rounded-xl border border-dashed
            border-zinc-700 bg-zinc-950
            p-8 transition
            hover:border-emerald-500/60
            hover:bg-emerald-500/[0.03]
          ">
            <div className="
              grid h-12 w-12 place-items-center
              rounded-xl bg-emerald-500/10
            ">
              <UploadCloud
                className="text-emerald-400"
                size={24}
              />
            </div>

            <span className="
              mt-3 text-sm font-medium
              text-zinc-200
            ">
              {file?.name ??
                "Seleccionar archivo .dxf"}
            </span>

            <span className="
              mt-1 text-xs text-zinc-500
            ">
              El archivo original se almacenará
              dentro de la base de datos
            </span>

            <input
              type="file"
              accept=".dxf"
              className="hidden"
              onChange={handleFileChange}
            />
          </label>
        </div>

        <div className="
          mt-5 flex flex-col justify-between
          gap-3 sm:flex-row sm:items-center
        ">
          <div className="text-xs text-zinc-500">
            {file
              ? `${(
                  file.size /
                  1024
                ).toFixed(1)} KB`
              : "Ningún archivo seleccionado"}
          </div>

          <button
            type="submit"
            disabled={loading}
            className="
              inline-flex items-center
              justify-center gap-2
              rounded-lg bg-emerald-500
              px-5 py-2.5 font-semibold
              text-zinc-950 transition
              hover:bg-emerald-400
              disabled:cursor-not-allowed
              disabled:opacity-50
            "
          >
            {loading ? (
              <LoaderCircle
                className="animate-spin"
                size={18}
              />
            ) : (
              <FileCode2 size={18} />
            )}

            {loading
              ? "Procesando DXF..."
              : "Procesar drawing"}
          </button>
        </div>
      </form>

      {/* Resultado */}

      {result && (
        <section className="space-y-5">
          <div className="
            rounded-2xl border
            border-emerald-500/30
            bg-emerald-500/10 p-5
          ">
            <div className="
              flex flex-col justify-between
              gap-4 md:flex-row
              md:items-center
            ">
              <div className="
                flex items-center gap-3
                text-emerald-400
              ">
                <CheckCircle2 size={24} />

                <div>
                  <div className="font-semibold">
                    {result.message}
                  </div>

                  <div className="
                    mt-1 text-xs
                    text-emerald-300/60
                  ">
                    {result.file_name}
                  </div>
                </div>
              </div>

              <div className="
                rounded-full border
                border-emerald-500/20
                bg-zinc-950/50 px-3 py-1
                text-xs font-medium
                text-emerald-400
              ">
                Drawing #{result.drawing_id}
              </div>
            </div>

            <div className="
              mt-5 grid grid-cols-2
              gap-3 md:grid-cols-4
            ">
              <ResultMetric
                label="ROIs"
                value={result.roi_count.toString()}
              />

              <ResultMetric
                label="Ancho"
                value={`${result.width_mm.toFixed(
                  2,
                )} mm`}
              />

              <ResultMetric
                label="Alto"
                value={`${result.height_mm.toFixed(
                  2,
                )} mm`}
              />

              <ResultMetric
                label="Revisión"
                value={result.revision}
              />
            </div>
          </div>

          {/* Preview */}

          <div className="
            overflow-hidden rounded-2xl
            border border-zinc-800
            bg-zinc-900/60 shadow-2xl
          ">
            <div className="
              flex items-center justify-between
              border-b border-zinc-800
              px-4 py-3
            ">
              <div className="
                flex items-center gap-2
              ">
                <ImageIcon
                  size={17}
                  className="text-emerald-400"
                />

                <span className="
                  text-sm font-medium
                ">
                  Preview del drawing
                </span>
              </div>

              {previewUrl && (
                <a
                  href={previewUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="
                    inline-flex items-center
                    gap-1.5 rounded-lg
                    border border-zinc-700
                    bg-zinc-950 px-3 py-1.5
                    text-xs text-zinc-300
                    transition hover:bg-zinc-800
                  "
                >
                  <Expand size={14} />

                  Abrir imagen
                </a>
              )}
            </div>

            <div className="
              relative min-h-[420px]
              bg-black
            ">
              {previewLoading && (
                <div className="
                  absolute inset-0 z-10
                  grid place-items-center
                  bg-zinc-950/70
                ">
                  <div className="text-center">
                    <LoaderCircle
                      className="
                        mx-auto animate-spin
                        text-emerald-400
                      "
                      size={36}
                    />

                    <p className="
                      mt-3 text-sm
                      text-zinc-400
                    ">
                      Cargando preview...
                    </p>
                  </div>
                </div>
              )}

              {previewError ? (
                <div className="
                  absolute inset-0
                  grid place-items-center
                ">
                  <div className="
                    max-w-sm text-center
                  ">
                    <ImageIcon
                      size={42}
                      className="
                        mx-auto text-zinc-700
                      "
                    />

                    <p className="
                      mt-3 text-sm
                      text-zinc-400
                    ">
                      No fue posible cargar el
                      preview del drawing.
                    </p>

                    <p className="
                      mt-1 text-xs
                      text-zinc-600
                    ">
                      Revisa que el endpoint
                      processed-image esté
                      registrado en FastAPI.
                    </p>
                  </div>
                </div>
              ) : (
                previewUrl && (
                  <img
                    src={previewUrl}
                    alt={`Preview de ${result.part_no}`}
                    onLoad={() =>
                      setPreviewLoading(false)
                    }
                    onError={() => {
                      setPreviewLoading(false);
                      setPreviewError(true);
                    }}
                    className="
                      h-full min-h-[420px]
                      w-full object-contain p-4
                    "
                  />
                )
              )}
            </div>

            <div className="
              flex flex-wrap items-center
              justify-between gap-3
              border-t border-zinc-800
              px-4 py-3
              text-xs text-zinc-500
            ">
              <span>
                Parte: {result.part_no}
              </span>

              <span>
                {Object.entries(
                  result.summary ?? {},
                )
                  .map(
                    ([type, count]) =>
                      `${type}: ${count}`,
                  )
                  .join(" · ")}
              </span>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}


function ResultMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="
      rounded-xl border
      border-emerald-500/10
      bg-zinc-950/60 p-3
    ">
      <div className="
        text-xs text-zinc-500
      ">
        {label}
      </div>

      <div className="
        mt-1 font-semibold
        text-zinc-100
      ">
        {value}
      </div>
    </div>
  );
}