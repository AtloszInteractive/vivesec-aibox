-- Print layout for the user manual: each chapter starts on a new page (the first one
-- right after the table of contents).
function Header(el)
  if el.level ~= 1 then
    return nil
  end
  return {
    pandoc.RawBlock("openxml", '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'),
    el,
  }
end
