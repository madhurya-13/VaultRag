import { zodResolver } from '@hookform/resolvers/zod'
import { useQueryClient } from '@tanstack/react-query'
import { FileUp, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { queryKeys } from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import {
  CLASSIFICATIONS,
  DEPARTMENTS,
  MAX_UPLOAD_BYTES,
} from '@/lib/constants'
import { formatBytes, titleCase } from '@/lib/utils'
import type { Document } from '@/types/api'

const uploadSchema = z.object({
  document_name: z
    .string()
    .trim()
    .min(1, 'Document name is required')
    .max(255, 'Document name must be 255 characters or fewer'),
  department: z.string().min(1, 'Select a department'),
  classification: z.enum(CLASSIFICATIONS),
  file: z
    .custom<FileList>((value) => value instanceof FileList && value.length === 1, {
      message: 'Select one PDF file',
    })
    .refine(
      (files) =>
        files?.[0]?.type === 'application/pdf' ||
        files?.[0]?.name.toLowerCase().endsWith('.pdf'),
      'Only PDF files are accepted',
    )
    .refine(
      (files) => (files?.[0]?.size ?? 0) <= MAX_UPLOAD_BYTES,
      `The PDF must be ${formatBytes(MAX_UPLOAD_BYTES)} or smaller`,
    ),
})

type UploadValues = z.infer<typeof uploadSchema>

export function UploadDialog({
  open,
  onOpenChange,
  onUploaded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onUploaded?: (document: Document) => void
}) {
  const queryClient = useQueryClient()
  const [fileLabel, setFileLabel] = useState('')
  const form = useForm<UploadValues>({
    resolver: zodResolver(uploadSchema),
    defaultValues: {
      document_name: '',
      department: 'ENGINEERING',
      classification: 'INTERNAL',
    },
  })

  useEffect(() => {
    if (!open) {
      form.reset()
      setFileLabel('')
    }
  }, [open, form])

  const onSubmit = form.handleSubmit(async (values) => {
    const formData = new FormData()
    formData.append('document_name', values.document_name)
    formData.append('department', values.department)
    formData.append('classification', values.classification)
    formData.append('file', values.file[0])

    try {
      const { data } = await api.post<Document>('/documents/upload', formData)
      await queryClient.invalidateQueries({ queryKey: queryKeys.documents })
      toast.success('Document uploaded', {
        description: `${data.document_name} is ready to be processed.`,
      })
      onUploaded?.(data)
      onOpenChange(false)
    } catch (error) {
      toast.error('Upload failed', { description: getErrorMessage(error) })
    }
  })

  const fileError = form.formState.errors.file?.message
  const busy = form.formState.isSubmitting

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-full border border-primary/25 bg-primary/10 text-primary">
            <FileUp className="size-5" aria-hidden="true" />
          </div>
          <DialogTitle>Upload a document</DialogTitle>
          <DialogDescription>
            The backend stores the PDF privately, derives the required clearance
            from its classification and queues it for extraction, chunking and
            embedding.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={onSubmit} noValidate>
          <FormField
            id="document_name"
            label="Document name"
            error={form.formState.errors.document_name?.message}
            required
          >
            <Input
              id="document_name"
              placeholder="e.g. Information Security Policy 2026"
              aria-invalid={Boolean(form.formState.errors.document_name)}
              {...form.register('document_name')}
            />
          </FormField>

          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              id="department"
              label="Department"
              error={form.formState.errors.department?.message}
              required
            >
              <Select id="department" {...form.register('department')}>
                {DEPARTMENTS.map((department) => (
                  <option key={department} value={department}>
                    {titleCase(department)}
                  </option>
                ))}
              </Select>
            </FormField>

            <FormField
              id="classification"
              label="Classification"
              error={form.formState.errors.classification?.message}
              required
            >
              <Select id="classification" {...form.register('classification')}>
                {CLASSIFICATIONS.map((classification) => (
                  <option key={classification} value={classification}>
                    {titleCase(classification)}
                  </option>
                ))}
              </Select>
            </FormField>
          </div>

          <FormField
            id="file"
            label="PDF file"
            error={fileError}
            description={`Maximum size ${formatBytes(MAX_UPLOAD_BYTES)}. The file is never served from a public directory.`}
            required
          >
            <label
              htmlFor="file"
              className="flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-background/40 px-4 py-6 text-center transition-colors hover:border-primary/40 hover:bg-primary/5"
            >
              <FileUp className="mb-2 size-5 text-muted-foreground" aria-hidden="true" />
              <span className="text-sm font-medium text-foreground">
                {fileLabel || 'Choose a PDF'}
              </span>
              <span className="mt-1 text-[11px] text-muted-foreground">
                Private upload · PDF only
              </span>
              <input
                id="file"
                type="file"
                accept="application/pdf,.pdf"
                className="sr-only"
                aria-invalid={Boolean(fileError)}
                {...form.register('file', {
                  onChange: (event) => {
                    setFileLabel(event.target.files?.[0]?.name ?? '')
                  },
                })}
              />
            </label>
          </FormField>

          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? (
                <>
                  <Loader2 className="animate-spin" aria-hidden="true" />
                  Uploading…
                </>
              ) : (
                <>
                  <FileUp aria-hidden="true" />
                  Upload document
                </>
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
