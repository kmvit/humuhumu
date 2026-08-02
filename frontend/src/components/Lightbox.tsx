// Просмотр полного изображения поверх страницы. Клик по фону или картинке — закрыть.
export default function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  return (
    <div className="lightbox" onClick={onClose} role="dialog" aria-modal="true">
      <img src={src} alt="" />
    </div>
  );
}
