import { useCallback, useEffect, useRef, useState } from "react";
import { WS_URL } from "../lib/api";

export function useCameraStream(enabled: boolean) {
  const [imageUrl, setImageUrl] = useState<string>();
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState("Cámara desconectada");
  const socketRef = useRef<WebSocket>();
  const objectUrlRef = useRef<string>();
  const reconnectRef = useRef<number>();
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const cleanupImage = useCallback(() => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = undefined;
  }, []);

  const disconnect = useCallback(() => {
    if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
    socketRef.current?.close();
    socketRef.current = undefined;
    setConnected(false);
  }, []);

 const connect = useCallback(() => {
  if (!enabledRef.current) {
    return;
  }

  const socketState =
    socketRef.current?.readyState;

  if (
    socketState === WebSocket.OPEN ||
    socketState === WebSocket.CONNECTING
  ) {
    return;
  }

  const socket = new WebSocket(
    `${WS_URL}/camera/ws?max_fps=15`,
  );

  socket.binaryType = "arraybuffer";
  socketRef.current = socket;

  socket.onopen = () => {
    setConnected(true);
    setMessage("Vista en vivo conectada");
  };

  socket.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      const nextUrl =
        URL.createObjectURL(
          new Blob(
            [event.data],
            {
              type: "image/jpeg",
            },
          ),
        );

      cleanupImage();

      objectUrlRef.current = nextUrl;
      setImageUrl(nextUrl);

      return;
    }

    try {
      const information = JSON.parse(
        String(event.data),
      );

      setMessage(
        information.message ??
          "Esperando cámara",
      );
    } catch {
      setMessage(
        "Mensaje de cámara recibido",
      );
    }
  };

  socket.onerror = () => {
    setMessage(
      "Error en WebSocket",
    );
  };

  socket.onclose = () => {
    setConnected(false);
    socketRef.current = undefined;

    if (enabledRef.current) {
      reconnectRef.current =
        window.setTimeout(
          connect,
          1500,
        );
    }
  };
}, [cleanupImage]);

  useEffect(() => {
    if (enabled) connect(); else disconnect();
    return () => { disconnect(); cleanupImage(); };
  }, [enabled, connect, disconnect, cleanupImage]);

  return { imageUrl, connected, message, connect, disconnect };
}
