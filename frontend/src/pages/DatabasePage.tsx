import {
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  Box,
  Boxes,
  CalendarDays,
  CheckCircle2,
  Database,
  ExternalLink,
  FileCode2,
  ImageIcon,
  InspectionPanel,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";

import {
  drawingProcessedImageUrl,
  getDrawings,
  getInspectionDetail,
  getInspections,
  getPartModel,
  getParts,
  inspectionOriginalImageUrl,
  inspectionOverlayImageUrl,
  inspectionRectifiedImageUrl,
} from "../lib/api";

import type {
  DrawingRecord,
  InspectionItemResult,
  InspectionRecord,
  PartModelResponse,
  PartRecord,
} from "../types/api";

import StatusBadge from "../components/StatusBadge";
import InspectionResultView from "../components/InspectionResultView";
import ZoomableImage from "../components/ZoomableImage";

// three.js pesa bastante: solo se descarga al abrir la vista 3D.
const PartViewer3D = lazy(() => import("../components/PartViewer3D"));


type DatabaseTab =
  | "parts"
  | "drawings"
  | "inspections";


type ImageViewerData = {
  title: string;
  url: string;
  /** Si se indica, se cargan los resultados por feature para los tooltips. */
  inspectionId?: number;
  showMarkers?: boolean;
};


export default function DatabasePage() {
  const [activeTab, setActiveTab] =
    useState<DatabaseTab>("inspections");

  const [parts, setParts] =
    useState<PartRecord[]>([]);

  const [drawings, setDrawings] =
    useState<DrawingRecord[]>([]);

  const [inspections, setInspections] =
    useState<InspectionRecord[]>([]);

  const [search, setSearch] =
    useState("");

  const [loading, setLoading] =
    useState(false);

  const [error, setError] =
    useState<string>();

  const [
    selectedInspection,
    setSelectedInspection,
  ] = useState<InspectionRecord>();

  const [
    imageViewer,
    setImageViewer,
  ] = useState<ImageViewerData>();

  const [
    viewer3D,
    setViewer3D,
  ] = useState<DrawingRecord>();


  useEffect(() => {
    loadDatabase();
  }, []);


  async function loadDatabase() {
    try {
      setLoading(true);
      setError(undefined);

      const [
        partsResponse,
        drawingsResponse,
        inspectionsResponse,
      ] = await Promise.all([
        getParts(),
        getDrawings(),
        getInspections(),
      ]);

      setParts(partsResponse.items ?? []);
      setDrawings(
        drawingsResponse.items ?? [],
      );
      setInspections(
        inspectionsResponse.items ?? [],
      );

    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "No fue posible consultar la base de datos.",
      );

    } finally {
      setLoading(false);
    }
  }


  const filteredParts = useMemo(() => {
    const term = search
      .trim()
      .toLowerCase();

    if (!term) {
      return parts;
    }

    return parts.filter((part) => {
      return (
        part.part_number
          .toLowerCase()
          .includes(term) ||
        part.revision
          .toLowerCase()
          .includes(term) ||
        (
          part.description ?? ""
        )
          .toLowerCase()
          .includes(term)
      );
    });
  }, [parts, search]);


  const filteredDrawings = useMemo(() => {
    const term = search
      .trim()
      .toLowerCase();

    if (!term) {
      return drawings;
    }

    return drawings.filter((drawing) => {
      return (
        drawing.part_number
          .toLowerCase()
          .includes(term) ||
        drawing.revision
          .toLowerCase()
          .includes(term) ||
        drawing.file_name
          .toLowerCase()
          .includes(term)
      );
    });
  }, [drawings, search]);


  const filteredInspections = useMemo(() => {
    const term = search
      .trim()
      .toLowerCase();

    if (!term) {
      return inspections;
    }

    return inspections.filter(
      (inspection) => {
        return (
          inspection.part_number
            .toLowerCase()
            .includes(term) ||
          inspection.revision
            .toLowerCase()
            .includes(term) ||
          inspection.inspector
            .toLowerCase()
            .includes(term) ||
          inspection.status
            .toLowerCase()
            .includes(term) ||
          inspection.id
            .toString()
            .includes(term)
        );
      },
    );
  }, [inspections, search]);


  const inspectionSummary = useMemo(() => {
    return inspections.reduce(
      (summary, inspection) => {
        if (
          inspection.status === "OK"
        ) {
          summary.ok += 1;
        } else if (
          inspection.status === "REVISAR"
        ) {
          summary.review += 1;
        } else if (
          inspection.status === "FALLO"
        ) {
          summary.fail += 1;
        }

        return summary;
      },
      {
        ok: 0,
        review: 0,
        fail: 0,
      },
    );
  }, [inspections]);


  return (
    <div className="space-y-6">
      {/* Header */}

      <div className="
        flex flex-col justify-between
        gap-4 md:flex-row md:items-end
      ">
        <div>
          <p className="
            text-sm font-medium
            text-emerald-400
          ">
            Optical Inspector Database
          </p>

          <h1 className="
            mt-1 text-3xl font-semibold
            tracking-tight
          ">
            Base de datos
          </h1>

          <p className="
            mt-2 max-w-2xl
            text-sm text-zinc-500
          ">
            Consulta partes, drawings e
            inspecciones registradas en la
            estación.
          </p>
        </div>

        <button
          type="button"
          disabled={loading}
          onClick={loadDatabase}
          className="
            inline-flex items-center
            justify-center gap-2 rounded-lg
            border border-zinc-700
            bg-zinc-900 px-4 py-2
            text-sm font-medium
            text-zinc-300 transition
            hover:bg-zinc-800
            disabled:opacity-40
          "
        >
          <RefreshCw
            size={16}
            className={
              loading
                ? "animate-spin"
                : ""
            }
          />

          Actualizar
        </button>
      </div>

      {/* Error */}

      {error && (
        <div className="
          rounded-xl border
          border-red-500/30
          bg-red-500/10 px-4 py-3
          text-sm text-red-300
        ">
          {error}
        </div>
      )}

      {/* Métricas */}

      <div className="
        grid gap-3
        sm:grid-cols-2
        xl:grid-cols-6
      ">
        <MetricCard
          title="Partes"
          value={parts.length}
          icon={<Boxes size={18} />}
          color="emerald"
        />

        <MetricCard
          title="Drawings"
          value={drawings.length}
          icon={<FileCode2 size={18} />}
          color="emerald"
        />

        <MetricCard
          title="Inspecciones"
          value={inspections.length}
          icon={
            <InspectionPanel size={18} />
          }
          color="emerald"
        />

        <MetricCard
          title="OK"
          value={inspectionSummary.ok}
          icon={<CheckCircle2 size={18} />}
          color="green"
        />

        <MetricCard
          title="Revisar"
          value={inspectionSummary.review}
          icon={<ShieldCheck size={18} />}
          color="amber"
        />

        <MetricCard
          title="Fallos"
          value={inspectionSummary.fail}
          icon={<X size={18} />}
          color="red"
        />
      </div>

      {/* Contenido */}

      <section className="
        overflow-hidden rounded-2xl
        border border-zinc-800
        bg-zinc-900/60 shadow-2xl
      ">
        {/* Tabs */}

        <div className="
          flex flex-col gap-4
          border-b border-zinc-800
          p-4 lg:flex-row
          lg:items-center
          lg:justify-between
        ">
          <div className="
            flex w-fit items-center
            gap-1 rounded-xl
            border border-zinc-800
            bg-zinc-950 p-1
          ">
            <TabButton
              active={activeTab === "parts"}
              label="Partes"
              icon={<Boxes size={16} />}
              onClick={() =>
                setActiveTab("parts")
              }
            />

            <TabButton
              active={
                activeTab === "drawings"
              }
              label="Drawings"
              icon={<FileCode2 size={16} />}
              onClick={() =>
                setActiveTab("drawings")
              }
            />

            <TabButton
              active={
                activeTab === "inspections"
              }
              label="Inspecciones"
              icon={
                <InspectionPanel
                  size={16}
                />
              }
              onClick={() =>
                setActiveTab(
                  "inspections",
                )
              }
            />
          </div>

          <div className="
            relative w-full lg:w-96
          ">
            <Search
              size={16}
              className="
                absolute left-3 top-1/2
                -translate-y-1/2
                text-zinc-500
              "
            />

            <input
              value={search}
              onChange={(event) =>
                setSearch(
                  event.target.value,
                )
              }
              placeholder={
                activeTab === "parts"
                  ? "Buscar número de parte..."
                  : activeTab ===
                      "drawings"
                    ? "Buscar drawing..."
                    : "Buscar inspección..."
              }
              className="
                w-full rounded-lg border
                border-zinc-700
                bg-zinc-950 py-2.5
                pl-9 pr-3 text-sm
                outline-none
                placeholder:text-zinc-700
                focus:border-emerald-500
                focus:ring-2
                focus:ring-emerald-500/15
              "
            />
          </div>
        </div>

        {loading ? (
          <LoadingDatabase />
        ) : (
          <>
            {activeTab === "parts" && (
              <PartsTable
                parts={filteredParts}
              />
            )}

            {activeTab === "drawings" && (
              <DrawingsTable
                drawings={
                  filteredDrawings
                }
                onOpen3D={setViewer3D}
                onOpenPreview={(
                  drawing,
                ) => {
                  setImageViewer({
                    title:
                      `Drawing ${drawing.part_number} Rev. ${drawing.revision}`,
                    url:
                      drawingProcessedImageUrl(
                        drawing.id,
                      ),
                  });
                }}
              />
            )}

            {activeTab ===
              "inspections" && (
              <InspectionsTable
                inspections={
                  filteredInspections
                }
                onSelect={
                  setSelectedInspection
                }
              />
            )}
          </>
        )}
      </section>

      {/* Inspection detail */}

      {selectedInspection && (
        <InspectionDetails
          inspection={selectedInspection}
          onClose={() =>
            setSelectedInspection(
              undefined,
            )
          }
          onOpenImage={setImageViewer}
        />
      )}

      {viewer3D && (
        <Viewer3DModal
          drawing={viewer3D}
          onClose={() =>
            setViewer3D(undefined)
          }
        />
      )}

      {/* Image modal */}

      {imageViewer && (
        <ImageViewer
          data={imageViewer}
          onClose={() =>
            setImageViewer(undefined)
          }
        />
      )}
    </div>
  );
}


function MetricCard({
  title,
  value,
  icon,
  color,
}: {
  title: string;
  value: number;
  icon: React.ReactNode;
  color:
    | "emerald"
    | "green"
    | "amber"
    | "red";
}) {
  const colors = {
    emerald:
      "bg-emerald-500/10 text-emerald-400",
    green:
      "bg-green-500/10 text-green-400",
    amber:
      "bg-amber-500/10 text-amber-400",
    red:
      "bg-red-500/10 text-red-400",
  };

  return (
    <div className="
      rounded-xl border border-zinc-800
      bg-zinc-900/60 p-4
    ">
      <div className="
        flex items-center
        justify-between
      ">
        <span className="
          text-xs text-zinc-500
        ">
          {title}
        </span>

        <div className={`
          grid h-8 w-8
          place-items-center
          rounded-lg
          ${colors[color]}
        `}>
          {icon}
        </div>
      </div>

      <div className="
        mt-3 text-2xl font-bold
      ">
        {value}
      </div>
    </div>
  );
}


function TabButton({
  active,
  label,
  icon,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`
        inline-flex items-center gap-2
        rounded-lg px-3 py-2
        text-sm font-medium
        transition
        ${
          active
            ? `
              bg-emerald-500/15
              text-emerald-400
              ring-1
              ring-emerald-500/20
            `
            : `
              text-zinc-400
              hover:bg-zinc-800
              hover:text-white
            `
        }
      `}
    >
      {icon}
      {label}
    </button>
  );
}


function PartsTable({
  parts,
}: {
  parts: PartRecord[];
}) {
  if (!parts.length) {
    return (
      <EmptyDatabase
        message="No se encontraron partes."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="
        w-full min-w-[800px]
        text-left text-sm
      ">
        <thead className="
          border-b border-zinc-800
          bg-zinc-950/50
          text-xs uppercase
          tracking-wider text-zinc-500
        ">
          <tr>
            <th className="px-5 py-3">
              ID
            </th>

            <th className="px-5 py-3">
              Número de parte
            </th>

            <th className="px-5 py-3">
              Revisión
            </th>

            <th className="px-5 py-3">
              Descripción
            </th>

            <th className="px-5 py-3">
              Fecha de registro
            </th>
          </tr>
        </thead>

        <tbody className="
          divide-y divide-zinc-800
        ">
          {parts.map((part) => (
            <tr
              key={part.id}
              className="
                transition
                hover:bg-zinc-800/40
              "
            >
              <td className="
                px-5 py-4
                font-mono text-xs
                text-zinc-500
              ">
                #{part.id}
              </td>

              <td className="
                px-5 py-4
                font-semibold
                text-zinc-100
              ">
                {part.part_number}
              </td>

              <td className="px-5 py-4">
                <span className="
                  rounded-md border
                  border-zinc-700
                  bg-zinc-950
                  px-2 py-1
                  text-xs text-zinc-300
                ">
                  {part.revision}
                </span>
              </td>

              <td className="
                max-w-md truncate
                px-5 py-4 text-zinc-400
              ">
                {part.description ||
                  "Sin descripción"}
              </td>

              <td className="
                px-5 py-4 text-zinc-500
              ">
                {formatDate(
                  part.created_at,
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function DrawingsTable({
  drawings,
  onOpenPreview,
  onOpen3D,
}: {
  drawings: DrawingRecord[];
  onOpenPreview: (
    drawing: DrawingRecord,
  ) => void;
  onOpen3D: (
    drawing: DrawingRecord,
  ) => void;
}) {
  if (!drawings.length) {
    return (
      <EmptyDatabase
        message="No se encontraron drawings."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="
        w-full min-w-[1050px]
        text-left text-sm
      ">
        <thead className="
          border-b border-zinc-800
          bg-zinc-950/50
          text-xs uppercase
          tracking-wider text-zinc-500
        ">
          <tr>
            <th className="px-5 py-3">
              Drawing
            </th>

            <th className="px-5 py-3">
              Parte
            </th>

            <th className="px-5 py-3">
              Archivo
            </th>

            <th className="px-5 py-3">
              Dimensiones
            </th>

            <th className="px-5 py-3">
              ROIs
            </th>

            <th className="px-5 py-3">
              Fecha
            </th>

            <th className="
              px-5 py-3 text-right
            ">
              Preview
            </th>
          </tr>
        </thead>

        <tbody className="
          divide-y divide-zinc-800
        ">
          {drawings.map((drawing) => (
            <tr
              key={drawing.id}
              className="
                transition
                hover:bg-zinc-800/40
              "
            >
              <td className="
                px-5 py-4
                font-mono text-xs
                text-zinc-500
              ">
                #{drawing.id}
              </td>

              <td className="px-5 py-4">
                <div className="
                  font-semibold
                  text-zinc-100
                ">
                  {drawing.part_number}
                </div>

                <div className="
                  mt-1 text-xs
                  text-zinc-500
                ">
                  Rev. {drawing.revision}
                </div>
              </td>

              <td className="
                px-5 py-4
                font-mono text-xs
                text-zinc-400
              ">
                {drawing.file_name}
              </td>

              <td className="
                px-5 py-4 text-zinc-400
              ">
                {drawing.width_mm.toFixed(
                  2,
                )}
                {" × "}
                {drawing.height_mm.toFixed(
                  2,
                )}
                {" mm"}
              </td>

              <td className="px-5 py-4">
                <span className="
                  rounded-full border
                  border-emerald-500/20
                  bg-emerald-500/10
                  px-2.5 py-1
                  text-xs font-medium
                  text-emerald-400
                ">
                  {drawing.roi_count}
                </span>
              </td>

              <td className="
                px-5 py-4 text-zinc-500
              ">
                {formatDate(
                  drawing.created_at,
                )}
              </td>

              <td className="
                px-5 py-4 text-right
              ">
                <button
                  type="button"
                  onClick={() =>
                    onOpen3D(drawing)
                  }
                  className="
                    mr-2 inline-flex items-center
                    gap-2 rounded-lg
                    border border-zinc-700
                    bg-zinc-950
                    px-3 py-2 text-xs
                    font-medium
                    text-zinc-300
                    hover:bg-zinc-800
                  "
                >
                  <Box size={14} />
                  Ver 3D
                </button>

                <button
                  type="button"
                  onClick={() =>
                    onOpenPreview(drawing)
                  }
                  className="
                    inline-flex items-center
                    gap-2 rounded-lg
                    border border-zinc-700
                    bg-zinc-950
                    px-3 py-2 text-xs
                    font-medium
                    text-zinc-300
                    hover:bg-zinc-800
                  "
                >
                  <ImageIcon size={14} />
                  Ver preview
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function InspectionsTable({
  inspections,
  onSelect,
}: {
  inspections: InspectionRecord[];
  onSelect: (
    inspection: InspectionRecord,
  ) => void;
}) {
  if (!inspections.length) {
    return (
      <EmptyDatabase
        message="No se encontraron inspecciones."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="
        w-full min-w-[1150px]
        text-left text-sm
      ">
        <thead className="
          border-b border-zinc-800
          bg-zinc-950/50
          text-xs uppercase
          tracking-wider text-zinc-500
        ">
          <tr>
            <th className="px-5 py-3">
              Inspección
            </th>

            <th className="px-5 py-3">
              Parte
            </th>

            <th className="px-5 py-3">
              Inspector
            </th>

            <th className="px-5 py-3">
              Score
            </th>

            <th className="px-5 py-3">
              Estado
            </th>

            <th className="px-5 py-3">
              ROIs
            </th>

            <th className="px-5 py-3">
              Fecha
            </th>

            <th className="
              px-5 py-3 text-right
            ">
              Detalles
            </th>
          </tr>
        </thead>

        <tbody className="
          divide-y divide-zinc-800
        ">
          {inspections.map(
            (inspection) => (
              <tr
                key={inspection.id}
                className="
                  transition
                  hover:bg-zinc-800/40
                "
              >
                <td className="
                  px-5 py-4
                  font-mono text-xs
                  text-zinc-500
                ">
                  #{inspection.id}
                </td>

                <td className="px-5 py-4">
                  <div className="
                    font-semibold
                    text-zinc-100
                  ">
                    {
                      inspection.part_number
                    }
                  </div>

                  <div className="
                    mt-1 text-xs
                    text-zinc-500
                  ">
                    Rev.{" "}
                    {inspection.revision}
                  </div>
                </td>

                <td className="px-5 py-4">
                  <div className="
                    flex items-center gap-2
                    text-zinc-300
                  ">
                    <UserRound
                      size={15}
                      className="
                        text-zinc-500
                      "
                    />

                    {inspection.inspector}
                  </div>
                </td>

                <td className="px-5 py-4">
                  <span className="
                    text-base font-semibold
                    text-emerald-400
                  ">
                    {inspection.score.toFixed(
                      1,
                    )}
                    %
                  </span>
                </td>

                <td className="px-5 py-4">
                  <StatusBadge
                    status={
                      inspection.status
                    }
                  />
                </td>

                <td className="
                  px-5 py-4 text-zinc-400
                ">
                  {inspection.roi_count}
                </td>

                <td className="
                  px-5 py-4 text-zinc-500
                ">
                  {formatDate(
                    inspection.created_at,
                  )}
                </td>

                <td className="
                  px-5 py-4 text-right
                ">
                  <button
                    type="button"
                    onClick={() =>
                      onSelect(inspection)
                    }
                    className="
                      inline-flex items-center
                      gap-2 rounded-lg
                      border border-zinc-700
                      bg-zinc-950
                      px-3 py-2 text-xs
                      font-medium
                      text-zinc-300
                      hover:bg-zinc-800
                    "
                  >
                    <ExternalLink
                      size={14}
                    />
                    Consultar
                  </button>
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}


function InspectionDetails({
  inspection,
  onClose,
  onOpenImage,
}: {
  inspection: InspectionRecord;
  onClose: () => void;
  onOpenImage: (
    data: ImageViewerData,
  ) => void;
}) {
  return (
    <section className="
      rounded-2xl border border-zinc-800
      bg-zinc-900/60 p-5
    ">
      <div className="
        flex items-start justify-between
        gap-4
      ">
        <div className="
          flex items-center gap-3
        ">
          <StatusBadge
            status={inspection.status}
          />

          <div>
            <h2 className="
              font-semibold
            ">
              Inspección #{inspection.id}
            </h2>

            <p className="
              mt-1 text-xs
              text-zinc-500
            ">
              {inspection.part_number}
              {" · Rev. "}
              {inspection.revision}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={onClose}
          className="
            grid h-9 w-9 place-items-center
            rounded-lg border
            border-zinc-700
            bg-zinc-950
            text-zinc-400
            hover:bg-zinc-800
            hover:text-white
          "
        >
          <X size={17} />
        </button>
      </div>

      <div className="
        mt-5 grid gap-4
        sm:grid-cols-2
        lg:grid-cols-4
      ">
        <DetailMetric
          label="Score"
          value={`${inspection.score.toFixed(
            2,
          )}%`}
        />

        <DetailMetric
          label="Inspector"
          value={inspection.inspector}
        />

        <DetailMetric
          label="Alineación"
          value={
            inspection.alignment_method
          }
        />

        <DetailMetric
          label="Inliers"
          value={
            inspection.alignment_inliers.toString()
          }
        />
      </div>

      <div className="
        mt-5 grid gap-3
        sm:grid-cols-3
      ">
        <ImageAction
          label="Resultado"
          onClick={() =>
            onOpenImage({
              title:
                `Resultado inspección #${inspection.id}`,
              url:
                inspectionOverlayImageUrl(
                  inspection.id,
                ),
              inspectionId: inspection.id,
              showMarkers: true,
            })
          }
        />

        <ImageAction
          label="Panel rectificado"
          onClick={() =>
            onOpenImage({
              title:
                `Panel rectificado #${inspection.id}`,
              url:
                inspectionRectifiedImageUrl(
                  inspection.id,
                ),
              inspectionId: inspection.id,
              showMarkers: true,
            })
          }
        />

        <ImageAction
          label="Imagen original"
          onClick={() =>
            onOpenImage({
              title:
                `Imagen original #${inspection.id}`,
              url:
                inspectionOriginalImageUrl(
                  inspection.id,
                ),
              inspectionId: inspection.id,
              showMarkers: false,
            })
          }
        />
      </div>

      <div className="
        mt-5 grid gap-3
        sm:grid-cols-3
      ">
        <SummaryCard
          label="OK"
          value={
            inspection.summary.OK ?? 0
          }
          color="green"
        />

        <SummaryCard
          label="REVISAR"
          value={
            inspection.summary.REVISAR ??
            0
          }
          color="amber"
        />

        <SummaryCard
          label="FALLO"
          value={
            inspection.summary.FALLO ??
            0
          }
          color="red"
        />
      </div>
    </section>
  );
}


function DetailMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="
      rounded-xl border border-zinc-800
      bg-zinc-950 p-4
    ">
      <div className="
        text-xs text-zinc-500
      ">
        {label}
      </div>

      <div className="
        mt-1 truncate font-semibold
      ">
        {value}
      </div>
    </div>
  );
}


function ImageAction({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="
        flex items-center gap-3
        rounded-xl border
        border-zinc-800
        bg-zinc-950 p-4
        text-left text-sm
        font-medium text-zinc-300
        transition hover:border
        hover:border-emerald-500/30
        hover:bg-emerald-500/5
        hover:text-emerald-400
      "
    >
      <ImageIcon size={18} />
      {label}
    </button>
  );
}


function SummaryCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color:
    | "green"
    | "amber"
    | "red";
}) {
  const colors = {
    green: "text-emerald-400",
    amber: "text-amber-400",
    red: "text-red-400",
  };

  return (
    <div className="
      rounded-xl border border-zinc-800
      bg-zinc-950 p-4 text-center
    ">
      <div className={`
        text-2xl font-bold
        ${colors[color]}
      `}>
        {value}
      </div>

      <div className="
        mt-1 text-xs text-zinc-500
      ">
        {label}
      </div>
    </div>
  );
}


function Viewer3DModal({
  drawing,
  onClose,
}: {
  drawing: DrawingRecord;
  onClose: () => void;
}) {
  const [model, setModel] =
    useState<PartModelResponse>();
  const [error, setError] =
    useState<string>();

  useEffect(() => {
    getPartModel(drawing.id)
      .then(setModel)
      .catch((caught) =>
        setError(
          caught instanceof Error
            ? caught.message
            : "No fue posible cargar el modelo.",
        ),
      );
  }, [drawing.id]);

  return (
    <div
      onClick={onClose}
      className="
        fixed inset-0 z-[100]
        grid place-items-center
        bg-black/85 p-4
        backdrop-blur-sm
      "
    >
      <div
        onClick={(event) =>
          event.stopPropagation()
        }
        className="
          flex max-h-[94vh]
          w-full max-w-7xl
          flex-col overflow-hidden
          rounded-2xl border
          border-zinc-700
          bg-zinc-950 shadow-2xl
        "
      >
        <div className="
          flex items-center
          justify-between
          border-b border-zinc-800
          px-4 py-3
        ">
          <div className="
            flex items-center gap-2
          ">
            <Box
              size={17}
              className="text-emerald-400"
            />
            <span className="text-sm font-medium">
              {`Modelo 3D · ${drawing.part_number} Rev. ${drawing.revision}`}
            </span>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="
              grid h-8 w-8
              place-items-center
              rounded-lg
              text-zinc-400
              hover:bg-zinc-800
              hover:text-white
            "
          >
            <X size={18} />
          </button>
        </div>

        <div className="p-4">
          {error ? (
            <p className="text-sm text-red-300">
              {error}
            </p>
          ) : !model ? (
            <div className="grid h-[70vh] place-items-center">
              <LoaderCircle
                size={28}
                className="animate-spin text-emerald-400"
              />
            </div>
          ) : (
            <Suspense
              fallback={
                <div className="grid h-[70vh] place-items-center">
                  <LoaderCircle
                    size={28}
                    className="animate-spin text-emerald-400"
                  />
                </div>
              }
            >
              <PartViewer3D
                className="h-[72vh]"
                model={model}
              />
            </Suspense>
          )}
        </div>
      </div>
    </div>
  );
}


function ImageViewer({
  data,
  onClose,
}: {
  data: ImageViewerData;
  onClose: () => void;
}) {
  const [items, setItems] =
    useState<InspectionItemResult[]>([]);

  useEffect(() => {
    setItems([]);
    if (data.inspectionId == null) return;
    getInspectionDetail(data.inspectionId)
      .then((detail) => setItems(detail.items ?? []))
      .catch(() => undefined);
  }, [data.inspectionId]);

  return (
    <div
      onClick={onClose}
      className="
        fixed inset-0 z-[100]
        grid place-items-center
        bg-black/85 p-4
        backdrop-blur-sm
      "
    >
      <div
        onClick={(event) =>
          event.stopPropagation()
        }
        className="
          flex max-h-[94vh]
          w-full max-w-7xl
          flex-col overflow-hidden
          rounded-2xl border
          border-zinc-700
          bg-zinc-950 shadow-2xl
        "
      >
        <div className="
          flex items-center
          justify-between
          border-b border-zinc-800
          px-4 py-3
        ">
          <div className="
            flex items-center gap-2
          ">
            <ImageIcon
              size={17}
              className="
                text-emerald-400
              "
            />

            <span className="
              text-sm font-medium
            ">
              {data.title}
            </span>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="
              grid h-8 w-8
              place-items-center
              rounded-lg
              text-zinc-400
              hover:bg-zinc-800
              hover:text-white
            "
          >
            <X size={18} />
          </button>
        </div>

        <div className="
          min-h-0 flex-1
          overflow-y-auto bg-zinc-950 p-4
        ">
          {data.inspectionId != null ? (
            <InspectionResultView
              url={data.url}
              alt={data.title}
              items={items}
              showMarkers={Boolean(data.showMarkers)}
              className="h-[70vh]"
            />
          ) : (
            <ZoomableImage
              src={data.url}
              alt={data.title}
              className="h-[80vh] rounded-xl"
            />
          )}
        </div>
      </div>
    </div>
  );
}


function EmptyDatabase({
  message,
}: {
  message: string;
}) {
  return (
    <div className="
      grid min-h-80 place-items-center
    ">
      <div className="
        text-center
      ">
        <Database
          size={42}
          className="
            mx-auto text-zinc-700
          "
        />

        <p className="
          mt-3 text-sm
          text-zinc-500
        ">
          {message}
        </p>
      </div>
    </div>
  );
}


function LoadingDatabase() {
  return (
    <div className="
      grid min-h-80 place-items-center
    ">
      <div className="
        text-center
      ">
        <LoaderCircle
          size={36}
          className="
            mx-auto animate-spin
            text-emerald-400
          "
        />

        <p className="
          mt-3 text-sm
          text-zinc-500
        ">
          Consultando base de datos...
        </p>
      </div>
    </div>
  );
}


function formatDate(
  value: string | null | undefined,
): string {
  if (!value) {
    return "Sin fecha";
  }

  const date = new Date(value);

  if (
    Number.isNaN(date.getTime())
  ) {
    return value;
  }

  return new Intl.DateTimeFormat(
    "es-MX",
    {
      dateStyle: "medium",
      timeStyle: "short",
    },
  ).format(date);
}