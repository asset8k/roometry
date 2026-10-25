import mark from '../../assets/roometry-mark.svg'

/** Decorative mark: the surrounding home link supplies the accessible brand name. */
export default function BrandMark() {
  return <span className="brand-mark" aria-hidden="true"><img src={mark} width="48" height="48" alt="" /></span>
}
